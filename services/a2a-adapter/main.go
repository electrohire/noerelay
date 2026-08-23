// NoeRelay A2A adapter translates the official A2A protocol into calls to the
// Rust authority gateway. It deliberately owns no routing or release policy.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"iter"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"
)

const maximumResponseBytes = 4 * 1024 * 1024

type config struct {
	listenAddress     string
	externalBaseURL   string
	noerelayBaseURL   string
	noerelayAPIKey    string
	inboundBearerKey  string
	principalID       string
	requestTimeout    time.Duration
	maximumInputBytes int
}

func configFromEnvironment() (config, error) {
	value := config{
		listenAddress:     envOr("NOERELAY_A2A_LISTEN", "127.0.0.1:8090"),
		externalBaseURL:   strings.TrimRight(envOr("NOERELAY_A2A_EXTERNAL_BASE_URL", "http://127.0.0.1:8090"), "/"),
		noerelayBaseURL:   strings.TrimRight(envOr("NOERELAY_INTERNAL_URL", "http://127.0.0.1:8080"), "/"),
		noerelayAPIKey:    os.Getenv("NOERELAY_API_KEY"),
		inboundBearerKey:  os.Getenv("NOERELAY_A2A_BEARER_KEY"),
		principalID:       envOr("NOERELAY_A2A_PRINCIPAL_ID", "a2a-adapter"),
		requestTimeout:    120 * time.Second,
		maximumInputBytes: 1024 * 1024,
	}
	if len(value.noerelayAPIKey) < 32 {
		return config{}, errors.New("NOERELAY_API_KEY must contain at least 32 characters")
	}
	if len(value.inboundBearerKey) < 32 {
		return config{}, errors.New("NOERELAY_A2A_BEARER_KEY must contain at least 32 characters")
	}
	if value.principalID == "" {
		return config{}, errors.New("NOERELAY_A2A_PRINCIPAL_ID is required")
	}
	return value, nil
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

type relayExecutor struct {
	config config
	client *http.Client
}

var _ a2asrv.AgentExecutor = (*relayExecutor)(nil)

func (executor *relayExecutor) Execute(ctx context.Context, execCtx *a2asrv.ExecutorContext) iter.Seq2[a2a.Event, error] {
	return func(yield func(a2a.Event, error) bool) {
		if execCtx == nil || execCtx.Message == nil {
			yield(nil, errors.New("A2A execution message is required"))
			return
		}
		input, err := messageText(execCtx.Message, executor.config.maximumInputBytes)
		if err != nil {
			yield(nil, err)
			return
		}
		if !yield(a2a.NewSubmittedTask(execCtx, execCtx.Message), nil) {
			return
		}
		if !yield(a2a.NewStatusUpdateEvent(execCtx, a2a.TaskStateWorking, nil), nil) {
			return
		}
		output, err := executor.invokeNoeRelay(ctx, input, execCtx)
		if err != nil {
			yield(nil, err)
			return
		}
		if !yield(a2a.NewArtifactEvent(execCtx, a2a.NewTextPart(output)), nil) {
			return
		}
		yield(a2a.NewStatusUpdateEvent(execCtx, a2a.TaskStateCompleted, nil), nil)
	}
}

func (*relayExecutor) Cancel(_ context.Context, execCtx *a2asrv.ExecutorContext) iter.Seq2[a2a.Event, error] {
	return func(yield func(a2a.Event, error) bool) {
		yield(a2a.NewStatusUpdateEvent(execCtx, a2a.TaskStateCanceled, nil), nil)
	}
}

func messageText(message *a2a.Message, maximumBytes int) (string, error) {
	var builder strings.Builder
	for _, part := range message.Parts {
		if text := part.Text(); text != "" {
			if builder.Len() > 0 {
				builder.WriteByte('\n')
			}
			builder.WriteString(text)
		}
		if builder.Len() > maximumBytes {
			return "", errors.New("A2A text input exceeds the configured limit")
		}
	}
	if strings.TrimSpace(builder.String()) == "" {
		return "", errors.New("A2A message must contain a text part")
	}
	return builder.String(), nil
}

func (executor *relayExecutor) invokeNoeRelay(ctx context.Context, input string, execCtx *a2asrv.ExecutorContext) (string, error) {
	payload, err := json.Marshal(map[string]any{
		"model": "noerelay/epr-1",
		"input": input,
		"metadata": map[string]string{
			"a2a_task_id":    string(execCtx.TaskID),
			"a2a_context_id": execCtx.ContextID,
		},
	})
	if err != nil {
		return "", fmt.Errorf("encode NoeRelay request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, executor.config.noerelayBaseURL+"/v1/responses", bytes.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("construct NoeRelay request: %w", err)
	}
	request.Header.Set("Authorization", "Bearer "+executor.config.noerelayAPIKey)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-NoeRelay-User", executor.config.principalID)
	request.Header.Set("X-NoeRelay-Session", execCtx.ContextID)

	response, err := executor.client.Do(request)
	if err != nil {
		return "", fmt.Errorf("NoeRelay request failed: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maximumResponseBytes+1))
	if err != nil {
		return "", fmt.Errorf("read NoeRelay response: %w", err)
	}
	if len(body) > maximumResponseBytes {
		return "", errors.New("NoeRelay response exceeded the adapter limit")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("NoeRelay rejected the delegated task with status %d", response.StatusCode)
	}
	var result struct {
		Output []struct {
			Content []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"content"`
		} `json:"output"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", errors.New("NoeRelay returned an invalid response envelope")
	}
	var output strings.Builder
	for _, item := range result.Output {
		for _, content := range item.Content {
			if content.Type == "output_text" && content.Text != "" {
				output.WriteString(content.Text)
			}
		}
	}
	if output.Len() == 0 {
		return "", errors.New("NoeRelay returned no releasable text artifact")
	}
	return output.String(), nil
}

func requireBearer(next http.Handler, expected string) http.Handler {
	expectedHash := sha256.Sum256([]byte(expected))
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		value, ok := strings.CutPrefix(request.Header.Get("Authorization"), "Bearer ")
		actualHash := sha256.Sum256([]byte(value))
		if !ok || subtle.ConstantTimeCompare(actualHash[:], expectedHash[:]) != 1 {
			response.Header().Set("Content-Type", "application/json")
			response.WriteHeader(http.StatusUnauthorized)
			_, _ = response.Write([]byte(`{"error":"unauthorized"}`))
			return
		}
		next.ServeHTTP(response, request)
	})
}

func handler(value config) http.Handler {
	executor := &relayExecutor{
		config: value,
		client: &http.Client{
			Timeout: value.requestTimeout,
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
	requestHandler := a2asrv.NewHandler(executor)
	card := &a2a.AgentCard{
		Name:        "NoeRelay",
		Description: "Governed virtual-model and agent orchestration through the Rust NoeRelay authority core",
		SupportedInterfaces: []*a2a.AgentInterface{
			a2a.NewAgentInterface(value.externalBaseURL+"/invoke", a2a.TransportProtocolJSONRPC),
		},
		DefaultInputModes:  []string{"text"},
		DefaultOutputModes: []string{"text"},
		Capabilities:       a2a.AgentCapabilities{Streaming: true},
		Skills: []a2a.AgentSkill{{
			ID:          "governed_execution",
			Name:        "Governed execution",
			Description: "Compile, route, execute, verify, and ledger an AI task through NoeRelay",
			Tags:        []string{"governance", "coding", "verification", "audit"},
		}},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"status":"live","authority":"rust-delegated"}`))
	})
	mux.Handle("/invoke", requireBearer(a2asrv.NewJSONRPCHandler(requestHandler), value.inboundBearerKey))
	mux.Handle(a2asrv.WellKnownAgentCardPath, a2asrv.NewStaticAgentCardHandler(card))
	return mux
}

func main() {
	flag.Parse()
	value, err := configFromEnvironment()
	if err != nil {
		log.Fatal(err)
	}
	listener, err := net.Listen("tcp", value.listenAddress)
	if err != nil {
		log.Fatalf("bind A2A adapter: %v", err)
	}
	server := &http.Server{
		Handler:           handler(value),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      130 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("NoeRelay A2A adapter listening on %s", listener.Addr())
	if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
