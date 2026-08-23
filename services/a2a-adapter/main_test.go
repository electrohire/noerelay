package main

import (
	"context"
	"iter"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"
)

func TestMessageTextRejectsMissingAndOversizedText(t *testing.T) {
	if _, err := messageText(a2a.NewMessage(a2a.MessageRoleUser, a2a.NewDataPart(map[string]any{"x": 1})), 10); err == nil {
		t.Fatal("expected non-text input to fail")
	}
	if _, err := messageText(a2a.NewMessage(a2a.MessageRoleUser, a2a.NewTextPart("too long")), 3); err == nil {
		t.Fatal("expected oversized input to fail")
	}
}

func TestRequireBearerFailsClosed(t *testing.T) {
	handler := requireBearer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusNoContent)
	}), "a-key-that-is-at-least-32-characters")
	for _, header := range []string{"", "Bearer wrong"} {
		request := httptest.NewRequest(http.MethodPost, "/invoke", nil)
		request.Header.Set("Authorization", header)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Fatalf("expected 401 for %q, observed %d", header, response.Code)
		}
	}
}

func TestExecutorDelegatesToRustResponsesAPI(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/responses" {
			t.Errorf("unexpected path %s", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer a-noerelay-key-that-is-at-least-32-chars" {
			t.Error("NoeRelay service credential was not forwarded")
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"output":[{"content":[{"type":"output_text","text":"verified result"}]}]}`))
	}))
	defer upstream.Close()
	executor := &relayExecutor{
		config: config{
			noerelayBaseURL:   upstream.URL,
			noerelayAPIKey:    "a-noerelay-key-that-is-at-least-32-chars",
			principalID:       "agent-principal",
			maximumInputBytes: 1024,
		},
		client: &http.Client{Timeout: time.Second},
	}
	execCtx := &a2asrv.ExecutorContext{
		Message:   a2a.NewMessage(a2a.MessageRoleUser, a2a.NewTextPart("do the work")),
		TaskID:    "task-1",
		ContextID: "context-1",
	}
	var events []a2a.Event
	var observedError error
	for event, err := range executor.Execute(context.Background(), execCtx) {
		events = append(events, event)
		if err != nil {
			observedError = err
		}
	}
	if observedError != nil {
		t.Fatal(observedError)
	}
	if len(events) != 4 {
		t.Fatalf("expected four lifecycle events, observed %d", len(events))
	}
	artifact, ok := events[2].(*a2a.TaskArtifactUpdateEvent)
	if !ok || artifact.Artifact.Parts[0].Text() != "verified result" {
		t.Fatalf("unexpected artifact event: %#v", events[2])
	}
}

func collect(sequence iter.Seq2[a2a.Event, error]) ([]a2a.Event, error) {
	var events []a2a.Event
	for event, err := range sequence {
		if err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	return events, nil
}

func TestExecutorRejectsEmptyInputBeforeNetwork(t *testing.T) {
	executor := &relayExecutor{config: config{maximumInputBytes: 10}, client: http.DefaultClient}
	events, err := collect(executor.Execute(context.Background(), &a2asrv.ExecutorContext{
		Message: a2a.NewMessage(a2a.MessageRoleUser, a2a.NewTextPart(strings.TrimSpace("   "))),
	}))
	if err == nil || len(events) != 0 {
		t.Fatalf("expected an early input error, got events=%d err=%v", len(events), err)
	}
}
