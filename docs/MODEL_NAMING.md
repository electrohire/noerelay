# AXIOVEX model naming policy

AXIOVEX public model releases use `axiovex-<name>-<major>.<minor>.<patch>-<variant>`.
The unversioned `axiovex-agni` identifier is the stable client-facing alias for the
current governed primary release. `axiovex-agni-recovery` is the deliberately
separate, high-capability recovery agent and is not selected by ordinary Agni traffic.

Major versions are assigned sequentially and restart at Agni after Zeus:

| Major | Name | Major | Name |
|---:|---|---:|---|
| 1 | Agni | 14 | Nabu |
| 2 | Brigid | 15 | Odin |
| 3 | Coyote | 16 | Prometheus |
| 4 | Daedalus | 17 | Quetzalcoatl |
| 5 | Enki | 18 | Ra |
| 6 | Freya | 19 | Seshat |
| 7 | Ganesha | 20 | Thoth |
| 8 | Hephaestus | 21 | Ukko |
| 9 | Iris | 22 | Vulcan |
| 10 | Janus | 23 | Woden |
| 11 | Krishna | 24 | Xolotl |
| 12 | Loki | 25 | Yama |
| 13 | Metis | 26 | Zeus |

Examples: `axiovex-agni-1.0.0`, `axiovex-agni-1.2.0-hybrid`,
`axiovex-brigid-2.0.0`, `axiovex-hephaestus-8.0.0-local`, and
`axiovex-zeus-26.0.0-enterprise`.

Variant identifiers must be lowercase ASCII words separated by hyphens. A major
version name is immutable after release. Aliases may advance only after the target
release passes the governed requirement, test, evaluation, and ledger gates.
