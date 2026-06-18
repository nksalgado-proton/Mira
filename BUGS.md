# BUGS.md

Running bug log for Mira. Opened 2026-06-17 during the event-recreation pass
on the Nuitka onefile build.

Report bugs by appending a new entry under **Open**. Copy the template, fill
what you can — even a one-line "X is broken" is fine; details can follow.
When fixed, move the entry to **Closed** with a short resolution note.

---

## INCIDENT 2026-06-17 — mira.db corruption crash + DB hardening

App crashed opening a day to Pick; afterwards "database is locked". Root cause
from mira.log: the user-store `mira.db` (`%LOCALAPPDATA%\Mira\mira.db`) was
CORRUPT — `database disk image is malformed` / integrity_check "Rowid out of
order". A query hit a bad page → unhandled `sqlite3.DatabaseError` → crash; the
"locked" after was the dead process still holding the writer lock. Storage is a
LOCAL disk, single PC (the "PC-Escritorio" in the lock log is this same
machine), so the corruption came from a crash/power-cut mid-write, antivirus,
or a write race during the big 2061-file Alaska import — NOT cloud sync.

Recovery tool: `recover_db.py` (read-only) — finds the newest CLEAN backup of
`mira.db` / each `event.db` and prints the exact restore commands.

Hardening applied (pendente de verificação no Windows — `verify.bat
tests\test_user_store_protection.py`):
- **H-1 auto-restore:** `UserStore.open` now auto-restores from the newest
  clean rolling backup when integrity_check fails, keeping the corrupt file
  aside as `mira.db.corrupt-<ts>` (mira/user_store/repo.py +
  protection.restore_from_backup/integrity_ok). Tests updated/added in
  tests/test_user_store_protection.py.
- **H-2 busy_timeout:** `PRAGMA busy_timeout = 5000` added to both connection
  setups (mira/store/schema.py + mira/user_store/schema.py) so momentary lock
  contention waits instead of raising/racing.
- **H-3 crash excepthook:** `sys.excepthook` in mira/ui/app.py logs the full
  traceback to mira.log + shows a dialog (incl. Qt-slot crashes), instead of
  dying silently in the windowed build.

- **Severity:** blocker / major / minor / polish
- **Phase:** Collect / Pick / Edit / Export / Share / wizard / build / other
- **Branch:** XMC / MC

---

## Template (copy this)

```
### B-NNN  <short title>
- Severity:
- Phase:
- Branch:
- Steps to reproduce:
  1.
- Expected:
- Actual:
- Notes / screenshots:
```

---

## Open

<!-- newest at top -->

### B-011  Videos nao tocam no Picker (apenas no .exe Nuitka)
- Severity: major
- Phase: Pick (Picker / PhotoViewport)
- Branch: XMC
- Steps to reproduce:
  1. Rodar o Mira.exe (build Nuitka onefile).
  2. Abrir um video no Picker e tentar reproduzir (Tab/transport).
- Expected: o video toca.
- Actual: nao toca. Fotos no MESMO Picker exibem normalmente (confirmado
  pelo Nelson). Roda so no .exe; do codigo (launch.bat) funciona.
- Notes / screenshots: causa = backend de midia do Qt nao embutido no
  bundle Nuitka. QMediaPlayer (mira/ui/media/photo_viewport.py) usa o
  ffmpegmediaplugin do Qt6 + DLLs av*/ffmpeg em PyQt6/Qt6/bin; com so
  --enable-plugin=pyqt6 a categoria "multimedia" (fora do conjunto
  "sensible") nao entra.
- FIX APLICADO (pendente de verificacao no Windows): adicionado
  --include-qt-plugins=sensible,multimedia em build_mira_with_nuitka.ps1 e
  build.bat (mantendo "sensible" para nao derrubar o plugin de plataforma
  qwindows). REBUILD necessario. VERIFICAR: tocar um video no Picker do
  .exe novo. Se AINDA falhar, bundlar explicitamente as DLLs
  avcodec/avformat/avutil/swscale/swresample de PyQt6\Qt6\bin (como os
  DLLs do ExifTool ja sao incluidos no .ps1).

### B-012  Datas de inicio/fim do evento nao deveriam ser input do usuario
- Severity: major (UX + invariant)
- Phase: other (criacao + edicao de evento; afeta Collect)
- Branch: XMC
- Steps to reproduce:
  1. Criar um evento.
  2. Reparar que From/To sao obrigatorios.
- Expected: as datas saem da tabela de dias do evento. Sem input do
  usuario na criacao; toda atualizacao da tabela de dias atualiza
  start_date/end_date na base.
- Actual: dialog exigia From/To, gate de Save bloqueava ate ambas
  preenchidas (spec/77 §5).
- Decisao Nelson (2026-06-17): "Vai para a base de dados extraido da
  tabela de dias apenas." Pure derive: `start_date = min(d.date for d
  in trip_days)`, `end_date = max(...)`. Manual override removido.
- FIX APLICADO (pendente de verificacao no Windows):
  - `mira/ui/pages/event_header_dialog.py`: removidos os QDateEdit
    From/To, o `_on_dates_changed`, o `_apply_existing` ja nao le
    start/end, e `header_info()` devolve `start_date: None` / `end_date:
    None` (chaves mantidas para compatibilidade dos callers). Save
    gating agora e Name + Type + Subtype.
  - `mira/gateway/gateway.py::Gateway.recompute_event_date_range(event_id)`:
    abre o event.db, le `trip_days`, escreve `start_date = min(dates)`
    e `end_date = max(dates)` via set_classification. Trip_days vazios
    => escreve "" (NULL na coluna).
  - Hooks chamando `recompute_event_date_range` apos cada batch de
    trip_days:
    - `main_window.py::_create_event_from_plan` (info-only + past-photos
      path)
    - `main_window.py::_record_collect_in_event_db` (mid-flow Collect
      upsert)
    - `main_window.py::_save_trip_day_edits` (plan editor)
    - `mira/ingest/engine.py` (ingest_event ao final do create_event)
    - `mira/ui/pages/preingest_dialog.py::_persist_plan`
    - `mira/ui/pages/new_event_page.py` apos create_event
  - Donut do Collect: TODOS os pontos onde `total_days` vira denominador
    ja estavam guarded contra zero (spec/77 §5 ja previa total_days=0
    fallback). `_event_tile.py::_collect_slices` retorna `(0, [(1.0,
    "track")])` quando total==0. `event_card.py` route para o
    placeholder "No plan yet" quando `total_days <= 0` antes de chegar
    no donut. Auditoria completa em [_event_card_data.py:255](mira/ui/pages/_event_card_data.py:255),
    `_event_tile.py:338`, `event_card.py:415/443/866/913`,
    `core/event_card_grid.py:92/147`, `_event_card_redesign.py:514-518`,
    `phases_page.py:805`.
  - Teste atualizado: `tests/test_event_tile_v2.py` substituiu
    `test_header_dialog_save_disabled_until_dates_set` por
    `test_header_dialog_save_gates_on_name_type_subtype_only` (Save
    libera sem dates; `header_info()` devolve None/None; campos
    `_from_edit`/`_to_edit` nao existem mais). 96 passes em
    tests/test_event_tile_v2.py + test_gateway.py + test_event_metrics.py
    + test_event_card_grid.py + test_event_classification.py.

### B-011  Subtype combo no header sem seta dropdown (nao parece dropdown)
- Severity: minor (UX descoberta)
- Phase: other (criacao de evento - EventHeaderDialog)
- Branch: XMC
- Steps to reproduce:
  1. Criar novo evento (menu Event > New, ou tile +).
  2. Olhar o campo Subtype no EventHeaderDialog.
- Expected: indicacao visual clara de que e um dropdown (seta `v`),
  para o usuario saber que ha opcoes ali.
- Actual: campo le como input de texto simples, sem seta. O placeholder
  "— select or type —" sumia na percepcao do usuario; Nelson achava que
  nao havia opcoes (apesar de o combo ter os 7+ items por trás).
- Notes / screenshots: `QComboBox#DesignSelect::drop-down` em
  `assets/themes/redesign.qss:81` redefinia a faixa do dropdown (`border:
  none; width: 22px`) mas NAO fornecia uma regra `::down-arrow`. Quando
  voce estiliza `::drop-down` no Qt, ele desliga a seta nativa; sem
  `::down-arrow` o glyph some.
- FIX APLICADO (pendente de verificacao no Windows):
  - Novo asset `assets/icons/glyphs/chevron_down.svg` (chevron 16x16,
    stroke #8b94a7 — combina com `ink_soft` dos dois temas).
  - `mira/ui/palette.py::build_redesign_qss` agora injeta
    `{chevron_down_icon_url}` como path POSIX absoluto (mesma estrategia
    do `check_icon_url` no `theme.py`, para funcionar no source + Nuitka
    onefile).
  - Regras `QComboBox#DesignSelect::down-arrow` (12x12) e
    `QComboBox#DaysCellSelect::down-arrow` (11x11) em
    `assets/themes/redesign.qss`, apontando para o SVG injetado.
  - Smoke: `EventHeaderDialog` renderizado em dark+light, seta visivel
    nos dois. No light o cinza fica um pouco mais fraco contra o card
    branco - usuario aprovou.
- TANGENTE (mesma sessao): aproveitei para expandir as listas de
  subtypes em `mira/event_classification.py::SUBTYPE_PRESETS` (Nelson
  2026-06-17 — `vamos expandir essa opcoes ai e criar tambem para os
  outros tipos`):
  - Trip: 7 -> 14 (+ Mountain, Safari, Cruise, Festival, Family
    vacation, Honeymoon, Workshop).
  - Session: 5 -> 14 (+ Newborn, Pet, Wildlife, Landscape, Sports,
    Street, Macro, Astrophotography, Studio).
  - Occasion: 5 -> 11 (+ Engagement, Baptism, Baby shower, Holiday,
    Reunion, Party).
  - Project: 4 -> 10 (+ Photobook, Exhibition, Portfolio, 365 daily,
    Self-portrait, Stock library).
  Unclassified continua vazio (preserva o gate Name+Type+Subtype do
  spec/64 §3.6). 16 passes em test_event_classification.py +
  test_classification_panel.py.

### B-010  Grid do Edit mostra simbologia picked/skipped (deveria so mostrar sobreviventes do Pick) [A CONFIRMAR]
- Severity: minor
- Phase: Edit
- Branch: XMC
- Steps to reproduce:
  1. Ir para a tela Edited (grid).
  2. Observar a simbologia das celulas.
- Expected: o grid do Edit so deveria mostrar o que sobreviveu ao Pick (os
  picked). Sem decisao a tomar aqui, logo nao faz sentido simbologia
  picked/skipped.
- Actual: aparece simbologia de picked/skipped; nao da para fazer selecao.
- Notes / screenshots: NELSON NAO TEM CERTEZA se e assim que esta - a
  confirmar. Se o grid ja filtra so os picked, a simbologia picked/skipped e
  redundante e deveria sair. Se ainda mostra skipped, ha um bug de filtro.
  Esclarecer o comportamento esperado vs. atual antes de corrigir.
- INVESTIGACAO (2026-06-17): o grid do Edit reusava
  `pick_days(phase="edit")`, que chama `_captured_by_day` SEM filtro de
  fase (mira/picked/model.py:775+131). Logo, o grid do Edit mostrava
  TODOS os itens captured do dia, NAO so os picked. A cor da borda vinha
  de `phase_state(phase="edit")`, que e o ledger Edit+Export
  compartilhado (spec/66 §1.1). Em Edit, `_apply_phase_chrome` ja
  escondia os botoes Pick/Skip all (creative-only), mas o handler
  `_on_grid_cell_border_clicked` ciclava Pick->Skip->Compare e a legenda
  mostrava "Picked / Skipped / Compare / Mixed".
  CONFLITO DE SPECS:
  - spec/59 §8: "border = marked for export" em Edit, click-toggle.
  - spec/66 §1.1 (mais recente): "Edit is purely creative [...] NO
    export buttons. Export reuses P/X: green=export, red=drop."
  DECISAO Nelson (2026-06-17): spec/66 puro - filtrar a picked + sem
  bordas + sem border-click cycle; clique = abrir editor.
- FIX APLICADO (pendente de verificacao no Windows):
  (a) `mira/ui/pages/days_grid_page.py::_refresh_from_gateway` agora
      passa `item_ids` para `day_grid_cells` quando o grid esta em
      modo Edit puro (phase="edit" e nao export_mode). Novo helper
      `_picked_item_ids_filter` constroi o set a partir do
      `phase_state("pick")`: explicit-picked sempre entra; quando o
      default da Pick e "picked", os items sem row tambem entram (default
      e default-Skip per spec, mas o helper cobre o caso configuravel).
  (b) Apos montar os GridItems, o caminho Edit-puro zera `it.state` para
      todos os items para que o Thumb pinte sem borda (sem
      picked/skipped/compare/mixed). A expansao de cluster
      (`_open_cluster`) tambem fixa state=None nos membros em Edit puro.
  (c) `_apply_phase_chrome` agora esconde a faixa de legenda
      Picked/Skipped/Compare/Mixed (`_legend_host` envolve a HBox) em
      Edit puro. Export mantem a legenda porque ainda usa as bordas.
  (d) `_on_grid_cell_border_clicked` em Edit puro: emite `item_activated`
      direto (drill-in), em vez de ciclar. Pick e Export continuam
      ciclando/toggle como antes (cluster cover ainda expande nos dois).
  (e) `keyPressEvent` em Edit puro: P/X/Space/C nao-op (creative-only,
      sem decisao por celula). Esc e Ctrl+Z continuam.
  Modos afetados: APENAS Edit puro (`phase == "edit" and not
  export_mode`). Pick (`phase="pick"`) e Export (`phase="export"`,
  internal `_phase="edit"` + `_export_mode=True`) ficam intocados.
  TESTES: tests/test_day_grid_model.py, test_day_grid_gateway.py,
  test_days_grid_export_mode.py, test_flow_layout.py - 74 passes.
  test_pick_model.py + test_external_returns.py + test_picked_media.py
  - 41 passes.
  NOTA: tambem corrigi um bug pre-existente no
  test_flow_layout.py::test_add_and_remove_mark_layout_dirty (Spy
  inicializava `self.invalidations` DEPOIS do `super().__init__`, mas
  o B-001 fix faz o `addItem` durante a construcao ja chamar invalidate).

### B-009  Abrir a fase Edit demora bastante sem feedback
- Severity: minor (melhoria de UX)
- Phase: Edit
- Branch: XMC
- Steps to reproduce:
  1. Passar para a fase Edit.
- Expected: indicacao de carregamento (spinner / progresso / cursor de
  espera) enquanto a fase abre.
- Actual: abre com demora consideravel, sem qualquer indicacao do que esta a
  fazer.
- Notes / screenshots: mesmo padrao do B-003 (Quick Sweep) e B-002 - operacao
  demorada sem feedback na UI. Esta a acumular-se um tema transversal: falta
  de indicadores de carregamento em transicoes/operacoes pesadas.
- FIX APLICADO (pendente de verificacao no Windows), em
  mira/ui/shell/main_window.py:
  - `_on_phase_activated("edit")` agora envolve a entrada toda
    (`_run_edit_entry_seams` + `_open_days_lists_for`) em
    `run_with_progress` (helper sancionado pelo spec/05 §4b). Mensagens
    de etapa: "Scanning for external returns..." -> "Refreshing
    picked-media links..." -> "Loading day list...". O usuario ve um
    dialogo modal + cursor de espera, com mensagem atualizada por
    etapa.
  - `_run_edit_entry_seams` ganhou um parametro `progress=None`
    (compativel com chamadas antigas). Quando ha callback, o seam emite
    mensagens via `progress(0, 0, msg)` e DEFERE o `_show_returns_box`
    (retornando `(report, show_nudge)`), para o pop nao stackear sobre
    o dialogo modal. Sem callback, mantem a forma original
    (mostra a caixa direto, retorna None).
  - Se a operacao falha, `_edit_phase_active` e limpo (assim um retry
    nao entra em "modo Edit" por acidente).

### B-008  Tile Pick 2x2 continua a constar como "not started"
- Severity: major
- Phase: Pick
- Branch: XMC
- Steps to reproduce:
  1. Fazer o pick de um dia.
  2. Ir ao Tile Pick na vista 2x2.
- Expected: o estado reflete o progresso de decisao (ja iniciado/decidido).
- Actual: continua a constar como "not started" mesmo apos o pick.
- Notes / screenshots: o indicador de estado do Tile Pick 2x2 nao le/atualiza
  a metrica de decisao. Possivel mesma raiz do B-004 (calculo/refresh do
  "decided / captured").
  ATUALIZACAO: depois de voltar a primeira tela (onde o status do Pick ja
  aparece correto), o 2x2 atualizou. Ou seja, o 2x2 so refresca apos o
  round-trip de navegacao, nao de imediato. Confirma o tema de refresh/
  invalidacao tardia (B-001/B-004) - falta um sinal para repintar o estado
  in-place sem precisar sair e voltar.
- FIX APLICADO (pendente de verificacao no Windows), em
  mira/ui/shell/main_window.py `_on_days_lists_back`: no caminho
  live (nao-QS), chamar `self.phases_page.set_event(self._current_event_id)`
  ANTES de mostrar o ACTIVITY_PAGE (a pagina Phases). Os tiles 2x2
  recomputam seu progresso imediatamente ao voltar da DaysLists para
  Phases, sem precisar de round-trip ate Events. Espelha o fix do B-004
  em `_on_days_grid_back`. O caminho QS per-event ja chamava `set_event`
  em `_qs_finalize_via_back`, entao nao precisou de mudanca.

### B-007  Cores da lista de dias demoram imenso a aparecer apos o pick
- Severity: major
- Phase: Pick
- Branch: XMC
- Steps to reproduce:
  1. Fazer o pick de um dia.
  2. Voltar/observar a lista de dias.
- Expected: as cores atualizam rapidamente apos as decisoes.
- Actual: demorou "um seculo" para as cores aparecerem na lista de dias.
- Notes / screenshots: relacionado ao B-004, mas aqui o foco e desempenho -
  a cor acaba por aparecer, mas muito lentamente. Verificar se o recalculo da
  metrica por dia varre tudo de novo / corre no thread da UI.
- PROVAVELMENTE RESOLVIDO PELO B-004 (a confirmar no Windows): a "demora de
  um seculo" era, na pratica, a lista so atualizar apos um round-trip completo
  ate Phases (porque _on_days_grid_back nao reconstruia). Com o fix do B-004,
  ao voltar do grid a lista reconstroi de imediato, entao as cores aparecem na
  volta em vez de exigir o desvio longo. SE ainda houver lentidao apos
  confirmar, o proximo passo e otimizar _build_day_snapshots (phase_day_progress
  + cached_buckets por dia + _fill_capture_hours) ou move-lo para fora do
  thread da UI / cachear por evento.

### B-006  Clicar perto da borda de uma celula do grid nao muda o status
- Severity: minor
- Phase: Pick
- Branch: XMC
- Steps to reproduce:
  1. Num grid (Pick), clicar perto da borda de uma celula/thumbnail.
- Expected: o clique muda o status (verde/vermelho = Pick/Skip), como ao
  clicar no centro.
- Actual: cliques perto da borda nao mudam mais o status.
- Notes / screenshots: a area "quente" do clique parece menor que a celula -
  margem/padding/gutter da celula a engolir o hit test. Verificar a hitbox da
  celula do grid (deveria cobrir a celula inteira, nao so a thumbnail/centro).
- CAUSA REAL: nao era hitbox - o grid do Pick foi migrado para ThumbGrid com
  two_zone_clicks=False (clique unico = drill-in no Picker), removendo o
  clique-na-borda-muda-status do grid legado. O status so mudava por teclado
  (P/X/Space/C).
- DECISAO Nelson (B-006): "Borda muda status, centro abre".
- FIX APLICADO (pendente de verificacao no Windows), em
  mira/ui/pages/days_grid_page.py:
  - ThumbGrid agora two_zone_clicks=True; ligado cell_border_clicked ->
    novo _on_grid_cell_border_clicked.
  - Borda: cluster -> expande; Export -> toggle verde/vermelho; Pick/Edit ->
    cicla Pick->Skip->Compare (gramatica de borda do §63, consistente com o
    visualizador de foto unica). Centro: comportamento atual (drill-in /
    toggle no Export). Tambem da setFocus na celula para os verbos de teclado
    seguintes mirarem nela.
  NOTA: escolhi CICLAR (P->S->Compare) para casar com a gramatica travada
  §63; se preferir so alternar verde<->vermelho (sem laranja), e troca de
  uma palavra ("cycle"->"toggle").

### B-005  Focus stack nao reconhecida como focus stack [WONTFIX — Nelson 2026-06-17]
- Severity: major
- Phase: Collect (ingest / classificacao)
- Branch: XMC
- Steps to reproduce:
  1. Ingerir / processar a pasta:
     D:\Photos\trips recovered\2025 - Sales Junior\stack corrected
- Expected: o conjunto e reconhecido como focus stack.
- Actual: nao foi reconhecido como focus stack.
- DIAGNOSTICO (diag_focus_stack.py, 2026-06-17): 367 frames sobreviventes
  de uma re-exportacao ("stack corrected") perderam TODOS os sinais que
  a deteccao consulta:
  - focus_bracket_tag_active : 0/367 (sem tag explicita de focus bracket)
  - focus_distance present   : 0/367 (impossivel inferir monotonia)
  - exposure_bracket_tag_active: 0/367
  - continuous_shooting_active: 0/367
  - sequence_number present  : 0/367
  Sobrevivem: timestamp + lens (OM 90mm F3.5) + body (DC-G9M2) +
  orientation. EXIF padrao OK, maker notes Panasonic apagados pela
  re-exportacao. Era um focus rail MANUAL (gaps 2-3s, ~14 min total, f/4
  1/30 ISO 200 constantes), nao o burst automatico do focus bracket
  in-camera. O detector forma 95 janelas mas todas sao REJECTED
  (ambiguous) - sem focus tag E sem FocusDistance, classificacao e
  impossivel. 367 > max_sequence_size=100 tambem seria problema,
  mas e secundario (a classificacao nunca chega la).
- DECISAO Nelson 2026-06-17: nada a fazer. A deteccao via EXIF e
  matematicamente impossivel nestes ficheiros (o tool que os "corrigiu"
  apagou tudo). Casos futuros de stacks pos-processados externamente
  ficam para um marcador manual / subpasta-como-stack se virar
  necessario.

### B-004  Lista de dias em Pick abre sem cores e nao atualiza com o status
- Severity: major
- Phase: Pick
- Branch: XMC
- Steps to reproduce:
  1. Entrar na fase Pick e abrir a lista de dias.
  2. Observar o indicador de cor de cada dia.
  3. Alterar o status (Pick/Skip) de fotos de um dia.
- Expected: a lista abre com 100% vermelho (nada decidido ainda) e a cor de
  cada dia atualiza conforme o progresso de decisao muda.
- Actual: abre sem cores nenhumas e nao atualiza quando altero o status.
- Notes / screenshots: dois problemas - (a) estado inicial nao renderiza a
  cor (deveria ser 100% vermelho = decided/captured = 0); (b) sem refresh do
  indicador apos mudanca de status. Pode partilhar a mesma raiz de repaint
  do B-001. Verificar o calculo da metrica "decided / captured" do Pick e o
  sinal que repinta o indicador por dia.
- DECISAO Nelson: deve refletir o default. Como Pick e default-Skip, abre
  tudo vermelho (igual ao Quick Sweep).
- FIX APLICADO (pendente de verificacao no Windows), em
  mira/ui/shell/main_window.py:
  (a) Cor inicial: o caminho live do Pick agora dobra os itens nao-decididos
      para o lado do default (default_state_for(settings,"pick") = Skip ->
      vermelho), via novo helper _apply_default_to_snapshots (generalizado a
      partir do _qs_apply_default_to_snapshots). Dia fresco abre 100% vermelho;
      o verde cresce conforme da Pick. Edit/Export NAO sao afetados (barras la
      significam developed/exported).
  (b) Refresh: _on_days_grid_back passou a reconstruir a lista
      (_open_days_lists_for) ao voltar do grid no caminho live (nao-QS), entao
      as decisoes feitas no grid/picker aparecem de imediato em vez de so apos
      um round-trip ate Phases. Isto tambem deve aliviar bastante o B-007.
  NOTA: os botoes Pick all/Skip all (por-dia e global) na lista ainda sao
  stubs (nao mudam status), entao ficam fora do escopo.

### B-003  Quick Sweep first demora a abrir a lista de dias, sem feedback
- Severity: minor (melhoria de UX)
- Phase: Collect (Quick Sweep)
- Branch: XMC
- Steps to reproduce:
  1. Selecionar "Quick Sweep first".
  2. A lista de dias demora bastante a abrir, sem nenhuma indicacao de que
     algo esta a acontecer.
- Expected: indicacao de carregamento (spinner / dialogo de progresso /
  cursor de espera) enquanto a lista de dias e construida.
- Actual: aparente "congelamento" - nada acontece visivelmente ate a lista
  abrir.
- Notes / screenshots: mesmo padrao do B-002 (operacao demorada sem feedback
  na UI). Verificar o que torna a abertura da lista de dias lenta e mover o
  trabalho pesado para fora do thread da UI se possivel.
- FIX APLICADO (pendente de verificacao no Windows): em
  MainWindow._run_quick_sweep_first (mira/ui/shell/main_window.py) a prep
  pesada (montar SourceItems + build_fast_days + ordenacao por dia) passou a
  correr dentro de run_with_progress, com mensagens de etapa: "Reading
  photos..." -> "Grouping into days..." -> "Sorting days...". Agora aparece
  um dialogo modal + cursor de espera ate a lista de dias abrir. Semantica de
  retorno preservada (None = backout, set() vazio = nada). NOTA: build_fast_days
  e uma chamada unica, entao a barra fica indeterminada durante o bucketing -
  mas o dialogo com mensagem aparece de imediato. VERIFICAR no app.

### B-002  Apagar evento + fotos trava 3-4s sem feedback
- Severity: minor (melhoria de UX)
- Phase: other (gestao de eventos / delete)
- Branch: XMC
- Steps to reproduce:
  1. Apagar um evento e escolher remover tambem as fotos.
  2. A operacao demora ~3-4s (depende da quantidade de fotos), sem feedback.
- Expected: um dialogo de progresso dizendo o que esta a fazer (ex.: "A
  remover N fotos..."), idealmente com barra/contador.
- Actual: a UI fica parada/sem resposta durante a remocao, sem indicacao.
- Notes / screenshots: considerar dialogo modal de progresso (ou cursor de
  espera no minimo) durante a remocao em massa; mover o trabalho para fora
  do thread da UI se estiver a bloquear.
- FIX APLICADO (pendente de verificacao no Windows): em
  MainWindow._on_delete_event (mira/ui/shell/main_window.py) o
  delete_event passou a correr atraves do helper sancionado
  run_with_progress (spec/05 §4b) - dialogo modal + cursor de espera +
  mensagem "Deleting "{name}" and {n} file(s)..." (ou "Removing ... from
  Mira..." no modo index-only). LIMITACAO: o rmtree e uma unica chamada
  bloqueante, entao a barra fica indeterminada (nao anima) durante a
  remocao - mas o dialogo+cursor+mensagem aparecem de imediato, que era a
  queixa. Para barra determinada seria preciso deletar incremental com
  callback de progresso no Gateway.delete_event (mais invasivo, adiado).

### B-001  Pagina de eventos so mostra o evento "closed" ate forcar redraw
- Severity: major
- Phase: other (navegacao / pagina inicial de eventos)
- Branch: XMC
- Steps to reproduce:
  1. Abrir um evento e ir para a pagina de fases (phases) do evento.
  2. Voltar para a pagina inicial de eventos.
  3. (Suspeita: acontece tambem ao voltar de outros lugares.)
- Expected: a pagina lista todos os eventos.
- Actual: so aparece o evento "closed" na tela. Redimensionar a janela
  (forcar um redraw) faz todos os eventos aparecerem.
- Notes / screenshots: cheira a problema de repaint/relayout do Qt - a lista
  nao e invalidada/atualizada ao reentrar na pagina; o resize dispara o
  repaint que entao mostra tudo. Verificar o sinal de refresh ao mostrar a
  pagina de eventos e se o layout chama update()/adjustSize().
- FIX APLICADO (pendente de verificacao no Windows): causa raiz no
  FlowLayout (mira/ui/base/flow_layout.py) - addItem/takeAt nao chamavam
  invalidate(), entao apos re-render (_render limpa e re-adiciona tiles) o
  Qt nao re-executava setGeometry/_do_layout; os tiles novos ficavam
  empilhados sem posicao em (0,0) e o tile "closed" (ordenado por ultimo)
  ficava por cima. Resize forcava o relayout. Adicionado self.invalidate()
  em addItem e takeAt + teste de regressao em tests/test_flow_layout.py
  (test_add_and_remove_mark_layout_dirty). Afeta tambem outros consumidores
  do FlowLayout (event_card, country_picker, thumb_grid, etc.) - todos
  beneficiam. VERIFICAR com: verify.bat tests\test_flow_layout.py


## Closed

<!-- move resolved entries here with a "Fixed:" line -->
