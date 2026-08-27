# Números oficiais: fonte única da verdade

**Atualizado:** 2026-07-20 (auditoria de vazamento/eco em censo; itens contaminados anulados)
**Julgador oficial:** `scripts/judge.py` (resolução por GeneID)
**Reproduzir:**
```bash
bash scripts/evaluate/judge_all.sh              # julga os 4 modelos
python3 scripts/audit/leakage_strict.py   # detecta vazamento do gabarito
python3 scripts/audit/echo_expanded.py       # detecta eco nos acertos expandidos
python3 scripts/evaluate/audited_accuracy.py # acurácia bruta e auditada
python3 scripts/audit/dose_response.py     # curva dose-resposta
python3 scripts/audit/error_direction.py      # direção do erro
```

> **Qualquer número em outro documento que divirja daqui está desatualizado.**
> Os documentos de trabalho escritos antes da correção do julgador tinham números defasados em
> ~4 p.p. e **não fazem parte deste repositório**. Só o que está aqui reflete o julgador corrigido.

---

## O que mudou no julgador (e por quê)

### Bug 1: resolução do gene pela string era ambígua

O matcher resolvia o gene a partir do **texto** do gabarito (`alias → canônico`), com "o último a
escrever vence". Aliases curtos são reivindicados por vários genes, e o resultado era caótico:

| Gabarito | Resolvia para | Correto |
|---|---|---|
| `FAS` | **FASN** | FAS |
| `Rac1` | **RNASE1** | RAC1 |
| `MDR1` | **TBC1D9** | ABCB1 |
| `brain natriuretic peptide` | **NPPA** | **NPPB** (NPPA é o peptídeo *atrial*!) |
| `tumor necrosis factor alpha` | **TNFAIP7** | TNF |

**133 de 4.000 gabaritos (4,6%) resolviam para o gene errado.**

**A correção:** o benchmark já carrega o `gene_id_gabarito` (GeneID do `gene2pubmed`/PubTator).
Usar o **GeneID** em vez da string elimina a ambiguidade por completo. Cobertura: 97,8% dos casos
(os 2,2% restantes são lncRNAs recentes e `LOC*` que o HGNC não indexa por GeneID, só nesses cai
no dicionário antigo).

### Bug 2: abreviação parentética aceitava gene diferente

`gold='metalloproteinase (MMP)-2'` → resposta `MMP-8` era **aceita**. MMP-8 é outro gene. Idem
`interleukin (IL)1alpha` → `IL1beta`.

**A correção:** o número e a letra grega da resposta precisam ser compatíveis com os do gabarito.

### Removida: a lista hardcoded de sinônimos

`KNOWN_SYNONYMS` era uma lista literal de 18 pares de genes. O problema não era só ser hardcoded:
**ela fora construída olhando as respostas dos modelos** (a estrutura era `gold → variantes que o
modelo respondeu`). Isso é ajustar a métrica aos dados de teste, só podia ajudar, nunca
prejudicar, logo inflava.

Ficou desnecessária com a resolução por GeneID (os aliases passam a vir do HGNC, autoridade externa
e independente das respostas). **Impacto de removê-la: −0,19 p.p.** Removida.

### Estado atual: nenhuma regra cita gene nenhum

| Regra | Tipo |
|---|---|
| 1. Match estrito + normalização grega/LaTeX | padrão geral (tabela α→alpha) |
| 2. Aliases do HGNC **via GeneID** | dados externos, inequívoco |
| 3. Abreviação parentética (com checagem de número/grego) | padrão geral (regex) |
| 4. Variantes de miRNA | padrão geral (regex) |
| 5. Prefixo `c-` | padrão geral (regex) |
| 6. Dicionário NEBB (fallback, só 2,2% dos casos) | dados |

**Zero entradas hardcoded.**

---

## ACURÁCIA

**Recalculada em 2026-07-20** com a auditoria em censo. Reproduzir:
`python3 scripts/evaluate/audited_accuracy.py`

**Denominador: 3.864 casos** (não 4.000). Os **136 itens contaminados**, abstracts que expõem
a resposta, foram **anulados**, saindo do benchmark para os 4 modelos, tenham eles acertado
ou não.

| Modelo | Q1 | Q2 | Q3 | Q4 | **Global** |
|---|---|---|---|---|---|
| **Llama 3.3 70B** | **61,2%** | **46,1%** | **30,2%** | 11,8% | **37,3%** |
| Gemma 4 Denso 31B | 62,1% | 43,4% | 21,6% | **12,6%** | 34,9% |
| Qwen 3.6 35B | 59,0% | 39,6% | 23,5% | 12,2% | 33,6% |
| Gemma 4 MoE 26B | 53,2% | 32,6% | 17,3% | 10,2% | 28,3% |

### As duas decisões metodológicas por trás desta tabela

**1. Anular a questão, não penalizar o modelo.** Uma pergunta cujo abstract expõe a resposta é
**defeituosa por construção**, está quebrada independentemente de quem acertou. Então ela sai
do benchmark inteiro, para todos os modelos. A alternativa (descontar o acerto mas manter a
pergunta como erro) penaliza o modelo **duas vezes**: perde o ponto *e* leva um erro por uma
questão que não deveria existir. Remover só para quem acertou daria denominadores diferentes
por modelo e quebraria a comparabilidade.

**2. Contaminação é semântica, não lexical.** Um item conta como contaminado se expõe **alguma**
forma de nomear o gene, não importa se o modelo emitiu exatamente aquela grafia. Inspeção dos
37 acertos em disputa não achou **um só** caso de recuperação independente: o texto expõe
`VGLUT2` e o modelo responde `SLC17A6`; expõe `cyclophilin A` e responde `PPIA`; expõe
`miRNA-575` e responde `miR-575`. Traduzir `VGLUT2` para `SLC17A6` exige ter lido `VGLUT2`.

### Robustez: as quatro combinações

`--convencao {semantica,estrita} --escopo {ambos,numerador}`, global do Llama 3.3 70B:

| | anular item (`ambos`) | descontar acerto (`numerador`) |
|---|---|---|
| **semântica** | **37,3%** ← oficial | 36,4% |
| estrita | 37,3% | 36,7% |

Amplitude total: **0,9 p.p.** O ranking é idêntico nas quatro combinações. **Nenhuma conclusão
depende da escolha**: o eixo `ambos`/`numerador` domina (0,6–0,9 p.p.), e o eixo
`semântica`/`estrita` some por completo quando o item é anulado (as duas dão 37,3%), porque
anular o item já remove todos os acertos dele, independentemente da grafia.

### O que mudou em relação à versão anterior desta tabela

A auditoria anterior descontava **168 ecos sobre uma população de 1.259** acertos expandidos
(julgador pré-correção do GeneID), estimava a taxa de falso positivo a partir de uma amostra de
22 casos sem seed, e mantinha o item contaminado no denominador. A nova identifica **136 itens
contaminados** em **censo** (todas as 127 questões de eco + 17 itens de vazamento estrito
revisados à mão) e os **anula**.

| Modelo | Global anterior | Global atual | Δ |
|---|---|---|---|
| Llama 3.3 70B | 36,9% | **37,3%** | +0,4 |
| Gemma 4 Denso 31B | 34,7% | **34,9%** | +0,2 |
| Qwen 3.6 35B | 33,4% | **33,6%** | +0,2 |
| Gemma 4 MoE 26B | 28,1% | **28,3%** | +0,2 |

**A acurácia sobe ligeiramente**, apesar de o desconto de acertos ter dobrado (168→344). A razão
é a mudança de denominador: anular a questão cobra do modelo **uma vez** (perde o acerto),
enquanto a convenção anterior cobrava **duas** (perdia o acerto *e* levava um erro por uma
pergunta defeituosa).

**O que muda de verdade não é o nível, é o gradiente**, que cresce de +0,4 p.p. para
**+1,6 a +2,2 p.p.** sobre o bruto, porque os itens anulados se concentram no Q4. E a
procedência: o número agora vem de censo com veredito rastreável caso a caso
(`docs/leakage_and_echo.md`), não de amostra pequena sem semente fixa.

### Brutos (antes do desconto do vazamento)

| Modelo | Q1 | Q2 | Q3 | Q4 | Global |
|---|---|---|---|---|---|
| Llama 3.3 70B | 62,1% | 48,2% | 31,6% | 14,9% | 39,2% |
| Gemma 4 Denso 31B | 63,0% | 45,3% | 22,8% | 15,1% | 36,5% |
| Qwen 3.6 35B | 59,8% | 41,2% | 24,3% | 14,6% | 35,0% |
| Gemma 4 MoE 26B | 54,2% | 34,8% | 18,8% | 13,1% | 30,2% |

### Gradiente Q1→Q4

| Modelo | Bruto (4.000) | **Auditado (3.864)** | Δ | Relativa |
|---|---|---|---|---|
| Gemma 4 Denso 31B | −47,9 p.p. | **−49,5 p.p.** | +1,6 | −79,7% |
| Llama 3.3 70B | −47,2 p.p. | **−49,4 p.p.** | +2,2 | −80,7% |
| Qwen 3.6 35B | −45,2 p.p. | **−46,8 p.p.** | +1,6 | −79,3% |
| Gemma 4 MoE 26B | −41,1 p.p. | **−43,0 p.p.** | +1,9 | −80,8% |

**A auditoria AUMENTA o gradiente nos quatro modelos** (+1,6 a +2,2 p.p.). O efeito por
estrato, média dos 4 modelos:

| Estrato | Bruta | Auditada | Queda |
|---|---|---|---|
| Q1 | 59,8% | 58,9% | −0,92 p.p. |
| Q2 | 42,4% | 40,4% | −1,93 p.p. |
| Q3 | 24,4% | 23,1% | −1,24 p.p. |
| **Q4** | 14,4% | 11,7% | **−2,71 p.p.** |

O Q4 perde **3× mais** que o Q1 em pontos absolutos. A razão é estrutural: em genes obscuros o
autor precisa apresentar a nomenclatura (*"proteína tal (SIGLA)"*, *"miR-524-3p"*), enquanto em
genes famosos escreve `TP53` e segue adiante. **A escrita científica correlaciona vazamento com
obscuridade.**

Confirma que **o vazamento é conservador**: ele *escondia* parte do efeito que a tese defende,
não o fabricava. É o oposto de viés de confirmação, e agora está demonstrado sobre a população
inteira, em censo.

**Ranking:** Llama 3.3 70B > Gemma 4 Denso 31B > Qwen 3.6 35B > Gemma 4 MoE 26B

---

## A DIREÇÃO DO ERRO (achado central)

`python3 scripts/audit/error_direction.py`

### Teste 1: pareado

| Modelo | n | Chute + popular | Razão mediana |
|---|---|---|---|
| Qwen 3.6 35B | 1.820 | **78,7%** | 3,8× |
| Gemma 4 Denso 31B | 1.672 | 78,2% | 3,6× |
| Llama 3.3 70B | 1.805 | 78,1% | **4,2×** |
| Gemma 4 MoE 26B | 1.727 | 75,3% | 3,2× |
| **Todos** | **7.024** | **77,6%** | **3,7×** |

### Teste 2: controle interno (a validação mais forte)

| Estrato | % chute + popular | Razão mediana |
|---|---|---|
| Q1 | **37,1%** (efeito **se inverte**) | **0,61×** |
| Q2 | 65,0% | 1,54× |
| Q3 | 87,5% | 4,08× |
| **Q4** | **95,5%** | **16,3×** |

O efeito **se inverte no Q1**, como tem de ser: se o alvo já é o TP53, é impossível chutar algo
mais popular. Um artefato do método não produziria essa inversão.

### Teste 3: não é cópia do contexto

| | n | Chute + popular | Razão |
|---|---|---|---|
| Chute **não** aparece no abstract | 6.085 | **78,6%** | 3,8× |
| Chute aparece no abstract | 939 | 71,1% | 2,7× |

O efeito é **mais forte** quando o gene chutado **não** está no texto.

### Teste 4: modelo nulo (mata a objeção de regressão à média)

| Mediana de publicações | |
|---|---|
| Todos os genes humanos (chute cego) | **1** |
| Genes-alvo destes erros | 54 |
| Genes **chutados** pelos modelos | **220** |

---

## GANHO SEMÂNTICO (% dos acertos obtidos via sinônimo)

**Auditado** (3.864 casos):

| Estrato | Llama 70B | Gemma Denso | Qwen | Gemma MoE |
|---|---|---|---|---|
| Q1 | 36,8% | 29,5% | 27,7% | 27,0% |
| Q2 | 32,3% | 26,6% | 26,3% | 28,4% |
| Q3 | 26,1% | 21,3% | 24,8% | 21,3% |
| **Q4** | **16,8%** | **15,7%** | **15,4%** | **17,3%** |
| **Global** | **31,7%** | **26,1%** | **25,7%** | **25,7%** |

> **ARGUMENTO RESSUSCITADO (2026-07-20).** Esta seção declarava o argumento *"a riqueza
> nomenclatural é subproduto da popularidade"* **morto**, por ausência de padrão monotônico.
> Com os dados **auditados** o padrão reaparece, agora limpo: **decresce nos quatro modelos**,
> de 27–37% no Q1 para 15–17% no Q4.
>
> **O que tinha acontecido:** o **eco** inflava os acertos expandidos da cauda longa, 55% dos
> acertos expandidos do Q4 eram sinônimo visível no próprio abstract. Isso mascarava a queda.
> Não era artefato do julgador, como se supôs; era contaminação do dado.

### Validação independente: riqueza nomenclatural no HGNC

Como a métrica acima é derivada dos acertos, e a auditoria removeu acertos expandidos
concentrados no Q4, há risco de circularidade. Testado de forma **independente**, contando
designações alternativas no HGNC sem olhar resultado de modelo nenhum:

| Estrato | Genes | Média de aliases | Mediana |
|---|---|---|---|
| Q1 | 1.000 | **6,29** | 6 |
| Q2 | 1.000 | 5,20 | 5 |
| Q3 | 998 | 4,13 | 4 |
| **Q4** | 914 | **2,48** | 2 |

Queda monotônica de **2,5×**. Soma de `alias_symbol` + `prev_symbol` + `alias_name` +
`prev_name`, resolvidos por GeneID. **As duas medidas convergem por caminhos independentes.**

---

## O que sobreviveu à correção do julgador

A auditoria de vazamento e eco vive em [`docs/leakage_and_echo.md`](../docs/leakage_and_echo.md)
(censo completo, 2026-07-20). É a fonte dos descontos aplicados na acurácia auditada. As análises
anteriores à correção do julgador (comparação de julgadores, primeira auditoria dos expandidos)
foram substituídas e ficaram fora deste repositório.

**Os argumentos que continuam de pé:**
1. Direção do erro (mais forte: 77,6%, 3,7×)
2. Falhas compartilhadas entre modelos
3. Piso de conhecimento
4. Curva dose-resposta
5. Vazamento é conservador (concentrado no Q4)
6. MoE não mitiga o viés
7. Ganho do NEBB decresce com a raridade, **RESSUSCITADO** (auditado + validação independente no HGNC)
