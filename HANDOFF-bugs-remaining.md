# HANDOFF — Bugs restantes (Mira)

Documento para passar a um novo agente continuar a leva de bugs que o Nelson
reportou enquanto recriava eventos (2026-06-17). O log mestre vive em
`BUGS.md` na raiz do repo; este handoff resume **só o que falta** e o contexto
necessário para agir sem reinvestigar.

---

## Estado da leva

| Bug | Tema | Estado |
|---|---|---|
| B-001 | Lista de eventos só mostra "closed" até resize | ✅ corrigido (FlowLayout.invalidate) |
| B-002 | Apagar evento+fotos sem feedback | ✅ corrigido (run_with_progress) |
| B-003 | Quick Sweep first sem feedback | ✅ corrigido (run_with_progress) |
| B-004 | Lista de dias do Pick sem cor / não atualiza | ✅ corrigido (default-fold + rebuild on back) |
| B-005 | Focus stack não reconhecida | ✅ wontfix (re-exportação apagou maker notes — detecção impossível por EXIF) |
| B-006 | Clique na borda do grid não muda status | ✅ corrigido (two-zone clicks) |
| B-007 | Cores do Pick demoram a aparecer | ✅ provavelmente coberto pelo B-004 (a confirmar) |
| B-008 | Tile Pick 2×2 fica "not started" até round-trip | ✅ corrigido (`_on_days_lists_back` agora chama `phases_page.set_event`) |
| B-009 | Abrir a fase Edit demora sem feedback | ✅ corrigido (Edit entry envolto em `run_with_progress`) |
| B-010 | Grid do Edit mostra simbologia picked/skipped | ✅ corrigido (Nelson decidiu spec/66 puro: filter to picked + sem bordas + sem cycle) |

Arquivos já tocados nesta leva: `mira/ui/base/flow_layout.py`,
`mira/ui/shell/main_window.py`, `mira/ui/pages/days_grid_page.py`,
`tests/test_flow_layout.py`, `diag_focus_stack.py`, `BUGS.md`,
`HANDOFF-bugs-remaining.md`.

**Toda a leva está fechada.** B-005 rodado em 2026-06-17 — diagnóstico
em `BUGS.md`: a re-exportação dos frames apagou os maker notes
Panasonic (focus_bracket tag + FocusDistance + sequence_number todos
zerados); a detecção via EXIF é matematicamente impossível, então
Nelson marcou como WONTFIX. Caminho futuro (se voltar): marcador manual
"subpasta-é-stack" no UI, respeitando spec/57 (Merged/).

---

## ⚠️ AVISOS para o agente (ler antes de mexer)

1. **Spec manda.** Reler `spec/`/CLAUDE.md antes de mudar gramática de UI. O
   keymap está TRAVADO (spec/63 §4): `P` Pick / `X` Skip / `Space` toggle /
   `C` cicla Pick→Skip→Compare; clique-na-borda **cicla** (no visualizador de
   foto única e — desde o B-006 — no grid).
2. **Decisões já tomadas pelo Nelson:**
   - B-004: a lista de dias deve refletir o default. Pick é default-Skip →
     dia fresco abre **100% vermelho**, verde cresce conforme dá Pick.
   - B-006: borda do grid **muda status**, centro **abre**. Foi implementado
     como CICLAR (P→S→Compare) para casar com §63; se Nelson quiser só
     alternar verde↔vermelho, trocar `"cycle"`→`"toggle"` em
     `days_grid_page._on_grid_cell_border_clicked`.
3. **Helper padrão para operações lentas:** `mira/ui/base/progress.py`
   `run_with_progress(parent, title, work, label=...)` — é "a única forma"
   (spec/05 §4b) de rodar trabalho pesado com diálogo modal. Usar para B-009.
4. **GLITCH DE MOUNT no sandbox:** o shell do sandbox serviu cópias
   **truncadas** de arquivos grandes (ex.: `main_window.py`, `days_grid_page.py`)
   — `ast.parse`/`py_compile` via shell deram erro FALSO em linhas longe das
   edições. As ferramentas de arquivo (Read/Edit) leem o arquivo real
   (lado Windows/D:) e mostraram tudo íntegro. **Verifique via Read, não
   confie no parse do shell.** Confira `git diff` antes de buildar.
5. **Não dá para rodar testes de GUI no sandbox** (falta `libEGL`, sem root).
   Validar no Windows: `verify.bat tests\...`.

---

## B-005 — Focus stack não reconhecida  (aguarda dados)

**Pasta:** `D:\Photos\trips recovered\2025 - Sales Junior\stack corrected`
**Pista do Nelson:** "muitos frames".

**Como detecção funciona:**
- `core/bracket_detector.py` — algoritmo de 2 passos (janela temporal+contexto,
  depois classificação). Thresholds: `DEFAULT_MAX_SEQUENCE_SIZE = 100`,
  `DEFAULT_MIN_SEQUENCE_SIZE = 3`, `DEFAULT_WINDOW_SECONDS = 2.0`.
- `core/bucket_scanner.py::_build_bracket_candidate_from_exif` — monta o
  `BracketCandidate` a partir do EXIF (tag de focus bracket via brand profile,
  `FocusDistance`, lens/body/orientation, etc.).
- `_same_context` exige lens_name NÃO-vazio + mesmo body + mesma orientação,
  senão recusa agrupar.

**Hipóteses (a confirmar com o diagnóstico):**
- (a) >100 frames → `max_sequence_size` corta a sequência em pedaços (a
  detecção existe mas vira 2+ stacks, ou confunde o usuário).
- (b) Os ficheiros "corrected"/re-salvos perderam o tag de focus bracket
  e/ou `FocusDistance` (maker notes somem no re-save) → nem o caminho
  explícito nem o inferido (monotonia de focus distance) disparam.
- (c) `lens_name` vazio nos ficheiros processados → `_same_context` não
  agrupa → nenhuma janela forma.

**PRÓXIMO PASSO:** pedir ao Nelson para rodar (na raiz do repo):
```
python diag_focus_stack.py "D:\Photos\trips recovered\2025 - Sales Junior\stack corrected"
```
O script (`diag_focus_stack.py`, já criado) roda o código REAL de detecção e
imprime: contagem de frames, presença de tag/FocusDistance/lens_name, o
windowing, as sequências e um diagnóstico. Com a saída:
- Se (a): considerar elevar/abolir o cap para o caminho de tag EXPLÍCITA de
  focus (câmeras modernas fazem 100-300+ frames); o cap existe sobretudo
  contra merges INFERIDOS descontrolados.
- Se (b)/(c): a detecção via EXIF é impossível nos ficheiros re-salvos —
  discutir um fallback (ex.: tratar uma subpasta inteira de N imagens com
  nomes sequenciais como um stack manual), respeitando spec/57 (Merged/).

---

## B-008 — Tile Pick 2×2 fica "not started" até round-trip

**Severidade:** major. **Fase:** Pick (PhasesPage / dashboard de fases).

**Sintoma:** após dar Pick num dia, o tile do Pick na vista 2×2 continua
"not started"; só atualiza depois de voltar à primeira tela (round-trip de
navegação). Mesmo tema de invalidação tardia do B-001/B-004.

**Onde olhar:**
- `mira/ui/pages/phases_page.py` — a PhasesPage com os tiles de fase
  (Collect/Pick/Edit/Export). É onde "not started" / progresso é calculado.
- `mira/ui/shell/main_window.py`:
  - `self.phases_page = PhasesPage(self.gateway)` (~L187).
  - `self.phases_page.set_event(event_id)` é o que (re)calcula os tiles —
    chamado em vários pontos de navegação (procurar todas as chamadas).
  - `_on_phase_tile_activated(phase)` (~L2612) abre a fase.
  - Voltar ao dashboard: `_on_days_lists_back` (~L2788) faz
    `page_stack.show_page(self._ACTIVITY_PAGE_KEY)` **sem** chamar
    `set_event` → os tiles ficam stale (provável causa raiz, espelha o B-004).

**Próximo passo proposto:** garantir que a PhasesPage recompute (chamar
`self.phases_page.set_event(self._current_event_id)`) ao RETORNAR ao
dashboard de fases após decisões — análogo ao fix do B-004 em
`_on_days_grid_back`. Verificar `_on_days_lists_back` e qualquer outro caminho
que mostre `_ACTIVITY_PAGE_KEY` sem refrescar. Conferir como a PhasesPage
deriva o estado por fase (provável `phase_day_progress()` / gateway) e se há
algum cache a invalidar.

---

## B-009 — Abrir a fase Edit demora sem feedback

**Severidade:** minor (UX). **Fase:** Edit. Mesmo padrão de B-002/B-003.

**Sintoma:** ao passar para o Edit, a abertura demora bastante sem qualquer
indicação do que está a fazer.

**Onde olhar:**
- `mira/ui/shell/main_window.py`:
  - `_on_phase_tile_activated(phase)` (~L2612) — ramo do Edit.
  - `self._edit_phase_active = True` (~L5697) — entrada da fase Edit; ver o
    bloco em volta para o trabalho pesado (provável scan/prep da fase Edit,
    talvez `mira/ui/edited/edit_prep.py` e/ou abrir days-lists em modo edit).
  - `_open_edit_surface_for_item` (~L3013) — abre a superfície de edição por
    item (pode ter prep de proxies/preview pesado).

**Próximo passo proposto:** identificar a chamada bloqueante na transição
para Edit e envolvê-la em `run_with_progress` (igual ao B-002/B-003), com
mensagem tipo "Preparing Edit…". Se a abertura por item (edit surface)
também travar, mesma técnica lá. Cuidado: `run_with_progress` roda no thread
da GUI e bombeia eventos — desabilitar o controle que dispara antes de
chamar (re-entrância).

---

## B-010 — Grid do Edit mostra simbologia picked/skipped  (CONFIRMAR primeiro)

**Severidade:** minor. **Fase:** Edit. Nelson NÃO tem certeza do estado atual.

**Questão:** o grid do Edit deveria mostrar **só os sobreviventes do Pick**
(os picked). Hoje aparece simbologia picked/skipped e não dá para selecionar.
Ou (i) o grid já filtra só os picked e a simbologia picked/skipped é
redundante (deveria sumir), ou (ii) ainda mostra skipped → bug de filtro.

**PRÓXIMO PASSO:** confirmar com o Nelson o comportamento esperado vs atual
ANTES de mexer. Depois:
- `mira/ui/pages/days_grid_page.py` é o mesmo grid usado em Pick/Edit/Export
  (`phase="edit"`). Ver `open_for_day(..., phase="edit")` e
  `day_grid_cells(eg, day_number, phase="edit")` (engine em `mira/picked/`)
  para saber se o conjunto já vem filtrado aos picked.
- Se filtra: esconder a borda de status picked/skipped no modo Edit (ela
  pertence ao Pick). Se não filtra: corrigir o filtro para excluir skipped.
- Spec relevante: `spec/59-edit-surface.md`, `spec/66` (Edit = developed÷picked).

---

## Sugestão de ordem

1. B-009 (rápido, isolado, padrão conhecido).
2. B-008 (provável fix análogo ao B-004 — refresh no retorno).
3. B-005 (assim que houver a saída do diagnóstico).
4. B-010 (após Nelson confirmar a expectativa).

Sempre: registrar o fix em `BUGS.md`, conferir `git diff`, e validar no
Windows com build + `verify.bat`.
