# Vazamento e eco: auditoria dos acertos da rodada v5

**Data:** 2026-07-20
**Escopo:** os 5.638 acertos da rodada v5 (4 modelos × 4.000 casos = 16.000 julgamentos).
**Resultado:** 136 itens contaminados anulados; benchmark auditado = **3.864 casos**.
**Pergunta:** quanto desses acertos o próprio texto entregou, em vez de ter vindo da memória
paramétrica do modelo?

> Este documento substitui a primeira auditoria dos expandidos (não publicada aqui), cujos números
> foram calculados sobre uma população menor (julgador pré-correção do GeneID). Ver §7.

---

## 1. Dois fenômenos distintos, que estavam sendo confundidos

O benchmark mascara o gene-alvo no abstract e pede que o modelo o recupere. O mascaramento
usa as anotações do PubTator: remove **exatamente a string que o PubTator anotou**, que é a
string que virou o gabarito.

Daí decorrem dois modos de falha, com mecanismos e magnitudes completamente diferentes:

| | **Vazamento estrito** | **Eco** |
|---|---|---|
| O que sobrevive no texto | o **próprio gabarito** | **outra forma de nomear** o mesmo gene |
| Por que sobreviveu | o gold ficou embutido em outro token (`KRAS` dentro de `KRASG12D`), que o PubTator anota como entidade diferente | o PubTator anotou uma forma e não a outra: `"nome completo (SIGLA)"` com só o nome anotado |
| Tipo de acerto afetado | estrito (resposta = gabarito literal) | expandido (resposta = sinônimo validado pelo NEBB) |
| Script | `scripts/audit/leakage_strict.py` | `scripts/audit/echo_expanded.py` |
| Magnitude | **0,9%** dos acertos estritos | **17,3%** dos acertos expandidos |

Em ambos os casos o pipeline é o mesmo: um detector automático **sinaliza** candidatos, e
todo candidato passa por **revisão humana** antes de virar número. Os vereditos ficam em CSV
versionado (`results/*_vereditos.csv`), nunca hardcoded em código.

**O eco é ~19× mais frequente que o vazamento estrito**, e a razão é estrutural: o
mascaramento foi construído para remover o gabarito, e faz isso bem. O que ele não cobre são
os *sinônimos*, e é exatamente sobre sinônimos que o NEBB concede o acerto expandido.

---

## 2. Definição operacional de eco

Um acerto expandido acontece quando o modelo **não** produziu o gabarito literal, mas produziu
um sinônimo que o NEBB reconhece como o mesmo gene canônico (`acerto_nebb=True` em
`judge.py`, com o alias responsável registrado em `alias_nebb`).

**Eco** é o caso em que esse sinônimo (ou a resposta verbatim do modelo) está **visível, sem
máscara, no próprio texto que o modelo leu**.

Casos reais, verificados manualmente:

| Gold (mascarado) | Resposta | O que ficou visível |
|---|---|---|
| `mir-4638` | `MicroRNA-4638-3p` | título: *"…functional role of **MicroRNA-4638-3p** in breast cancer"* |
| `Sortilin` | `NTSR2/gp95` | *"the identification of **gp95**[MASK], a sorting protein"* |
| `LINC01546` | `VAL` | *"lncRNA **VAL** (**V**imentin **A**ssociated lncRNA, [MASK])"* |
| `galactose-1-phosphate uridyltransferase` | `GALT` | *"deficiency in the [MASK] (**GALT**) enzyme"* |

O padrão é sempre o mesmo: o PubTator anotou **uma** forma de nomear o gene (virou o gold, foi
mascarada) e deixou **outra** visível. O modelo copia a visível, o NEBB credita como sinônimo
válido, e o acerto não mede memória, mede leitura.

---

## 3. A regra de detecção, passo a passo

Implementada em **`scripts/audit/echo_expanded.py`**. Para cada julgamento com
`acerto_nebb=True`:

**Passo 1: dois termos buscados, basta um casar.**
`alias_nebb` (o alias que justificou formalmente o crédito) e `resposta_modelo` (o que o modelo
escreveu, verbatim). As duas porque o alias vem em forma canônica e nem sempre é a grafia que
aparece no texto. Medido: 278 flags casam pelas duas, 26 só pelo alias, 6 só pela resposta.

**Passo 2: texto.**
O campo `prompt` do resultado bruto (`results/answers/answers_*.jsonl`),
que é literalmente o texto lido pelo modelo, **menos a linha de instrução**. Remover o
cabeçalho é por robustez. Um alias como `gene` casaria na instrução; medido, 0 dos 309 flags
vinham de lá.

> **Não usar `abstract_trecho`** do arquivo de julgamento: ele é truncado em ~200
> caracteres e perderia a maior parte dos ecos.

**Passo 3: normalização.**
`judge.normalize()` nos dois lados: letra grega → nome por extenso, comandos LaTeX,
minúsculas. Usar a **mesma** normalização do julgador é deliberado: o eco tem de ser medido no
mesmo espaço em que o crédito foi concedido.

**Passo 4: casamento como token inteiro.**

```
(?<![a-z0-9]) <termo escapado> (?![a-z0-9])
```

Sem a fronteira, `RELA` casaria dentro de *cor**rela**tes*, e foi exatamente o bug do detector
ingênuo da v2. Note que **hífen é fronteira**: por isso o alias `CYP` não casa dentro de
`CYP2C9`, mas a resposta `CYP2C9` casa.

**Passo 5: comprimento mínimo.** Termos com menos de 2 caracteres são ignorados.

### Reprodução

```bash
python3 scripts/audit/echo_expanded.py      # detecta; preserva os vereditos já escritos
python3 scripts/audit/echo_review_summary.py      # consolida o censo (127/127)
python3 scripts/audit/leakage_strict.py  # o outro fenômeno, para comparação
```

Os detectores são idempotentes: reexecutar **não** apaga revisão manual, e **avisa** se
apareceu unidade nova sem veredito.

---

## 4. Resultado da detecção automática

De **1.773 acertos expandidos**, **309 sinalizados como eco (17,4%)**:

| Estrato | Expandidos | Sinalizados | Taxa |
|---|---|---|---|
| Q1 Super Populares | 772 | 74 | 9,6% |
| Q2 Médios | 550 | 83 | 15,1% |
| Q3 Baixa Popularidade | 269 | 50 | 18,6% |
| **Q4 Cauda Longa** | **182** | **103** | **56,6%** |

Por modelo, a taxa é notavelmente estável (14,6% a 23,2%), o que já sugere que o eco é
propriedade do **item**, não do modelo, confirmado no passo seguinte.

---

## 5. A revisão manual

### 5.1 Por que a unidade é a questão, e não o acerto

Os 309 acertos sinalizados **não são independentes**: agrupam-se em **127 questões**
distintas `(pmid, gold)`, com média de **2,43 modelos por questão**.

| Modelos que ecoaram a mesma questão | Questões | Acertos |
|---|---|---|
| 1 | 35 | 35 |
| 2 | 32 | 64 |
| 3 | 29 | 87 |
| **4 (todos)** | **31** | **124** |

Só 28% das questões são caso de um modelo isolado; 31 questões foram ecoadas pelos **quatro**
modelos e sozinhas respondem por 40% dos acertos sinalizados.

Isso é o esperado e reforça a validade da medida: se um sinônimo do gene-alvo está escrito sem
máscara, **todo** modelo que lê o texto tende a produzi-lo. O veredito manual também é sobre o
item (*"o abstract deixou o sinônimo visível?"*), e por isso a revisão é feita por questão, não
repetida por modelo.

Consequência estatística: amostrar **acertos** trataria observações correlacionadas como
independentes e produziria IC otimista. A revisão é por **questão**.

> **A questão é a unidade certa também para o desconto.** Chegamos a considerar descontar
> apenas os acertos cuja *string* casou no texto (309), com o argumento de que outro modelo
> poderia ter respondido um sinônimo diferente, ausente do texto. **Isso foi descartado:** a
> inspeção dos 37 acertos em disputa mostrou que todos eram tradução trivial do termo exposto
> (`VGLUT2`→`SLC17A6`, `miRNA-575`→`miR-575`). A contaminação é **semântica**, não lexical:
> uma questão contaminada é contaminada para todos. Ver §9.2.

### 5.2 Cobertura: censo, não amostra

**As 127 questões foram revisadas: 100% da população.** Não há erro amostral, não há
ponderação, não há intervalo de confiança: a precisão do detector é uma **contagem**, não uma
estimativa.

A revisão foi feita em duas etapas. Primeiro uma amostra estratificada de 60 questões (15 por
estrato, `seed=31`), que deu 59/60 e um IC95% de 91,1–99,7%. Como a população inteira eram
apenas 127 questões, as 67 restantes foram revisadas em seguida e o censo fechou em **124/127**.
A amostra estava correta, o ponto estimado 98,3% caiu dentro do valor final de 99,0%, mas o
censo dispensa a discussão sobre qual precisão aplicar a qual estrato.

### 5.3 Critério do revisor

O revisor responde **"a string estava disponível para cópia?"**, e *não* "a string visível se
referia ao gene-alvo?".

É o critério **conservador**: se o token estava no texto sem máscara, não há como provar que a
resposta veio da memória paramétrica em vez da leitura, então o acerto não é creditado.

Casos em que os dois critérios divergem foram marcados com `AMBIGUO:` em `nota_revisor`, ver
§5.5, teste de sensibilidade.

### 5.4 Resultado do censo

| Estrato | Questões válidas | Acertos confirmados | Precisão |
|---|---|---|---|
| Q1 Super Populares | 28/30 | 72/74 | 97,3% |
| Q2 Médios | 36/36 | 83/83 | 100% |
| Q3 Baixa Popularidade | 20/20 | 50/50 | 100% |
| Q4 Cauda Longa | 40/41 | 101/102 | 99,0% |
| **TOTAL** | **124/127** | **306/309** | **99,0%** |

**Apenas 3 falsos alarmes em 309 acertos sinalizados**, e eles têm uma assinatura única:

| pmid | Termo buscado | Gold | Resposta | n_modelos |
|---|---|---|---|---|
| 24915949 | `igf` | `insulin-like growth factor-1` | `IGF-1` | 1 |
| 7683646 | `igf` | `IGF-I` | `IGF-II` | 1 |
| 9877162 | `pro` | `(pro)napsin B` | `pronapsin B` | 1 |

**Todos os três são termos de 3 caracteres, e todos os três foram sinalizados por um único
modelo.** O mecanismo é sempre o mesmo: o termo curto casa como *fragmento* (`igf` dentro de
"IGF binding", `pro` dentro de "(pro)napsin A"), enquanto a string da resposta não ocorre no
texto, verificado com contagem de casamentos no abstract inteiro.

Isso não é coincidência, e é um argumento de validade forte: **um casamento espúrio de
fragmento é idiossincrático de uma resposta; um eco real está no texto, e todo modelo que lê o
texto tende a produzi-lo.** Nas 31 questões ecoadas pelos **quatro** modelos, zero falsos
alarmes.

Vale registrar o caso que **quase** virou falso alarme e não era: gold `LINC01546`, resposta
`VAL`, termo de 3 letras, e em abstract biomédico "Val" costuma ser **valina**. A leitura do
abstract completo mostrou *"lncRNA VAL (Vimentin Associated lncRNA, [MASK])"*: é o gene, e o
nome por extenso está ao lado da máscara. Eco real. **A janela curta de contexto teria errado
esse veredito**: a revisão foi feita sobre o abstract inteiro.

### 5.5 Sensibilidade ao critério

8 questões (15 acertos) marcadas `AMBIGUO`: a string respondida está no texto, mas ali nomeia
outro gene da família ou a doença: `IL1` em *"IL1 receptor agonist"* (que é IL1RN), `KIT` em
*"KIT ligand"*, `IFNGR` em *"IFNGR-2"*, `Prx` nomeando a família e não PRDX6, `BHD` e `EDA` como
siglas de doença, `TPRKB` (que é gene diferente do gold `TP53RK`, erro do NEBB, não do
detector).

| Critério | Acertos confirmados | Precisão |
|---|---|---|
| (A) a string estava disponível, **adotado** | 306/309 | **99,0%** |
| (B) a string se referia ao alvo | 291/309 | 94,2% |

**A conclusão não muda em nenhum dos dois.**

---

## 6. Eco confirmado: os números que entram na acurácia auditada

Como é censo, o eco confirmado é a contagem direta, sem multiplicador de precisão:

| Estrato | Acertos expandidos | Eco confirmado | Taxa |
|---|---|---|---|
| Q1 Super Populares | 772 | 72 | 9,3% |
| Q2 Médios | 550 | 83 | 15,1% |
| Q3 Baixa Popularidade | 269 | 50 | 18,6% |
| **Q4 Cauda Longa** | **182** | **101** | **55,5%** |
| **Total** | **1.773** | **306** | **17,3%** |

### Nota histórica: a discussão sobre qual precisão aplicar

Enquanto só a amostra de 60 estava revisada, havia uma decisão em aberto: aplicar a precisão
por estrato (93,3% no Q1, 100% nos demais) ou a agregada (98,3%) a todos? A resposta era
**agregada**, por três razões que continuam valendo como método:

1. A diferença entre estratos não era significativa: Fisher exato bicaudal, Q1 (14/15) contra
   o resto (45/45), **p = 0,25**.
2. `15/15` tem IC95% de **[79,6%, 100%]**. Afirmar 100% para um estrato inteiro a partir de
   n=15 não se sustenta.
3. Usar estimativas por estrato deixaria um único caso mover a acurácia oficial do Q1,
   sobreajuste a ruído amostral.

**O censo tornou a discussão obsoleta**, e confirmou a decisão: a precisão real (99,0%) ficou
próxima do agregado (98,3%) e longe do 93,3% que a leitura por estrato teria imposto ao Q1.
O valor final do Q1 é **97,3%**, não 93,3%.

**Comparação com a auditoria antiga (95%).** A anterior usava 21/22 ≈ 95,5%, sobre uma amostra
pequena e sem seed. O 99,0% atual não é resultado novo, **é o mesmo fenômeno, medido sobre a
população inteira em vez de uma amostra de 22.**

---

## 7. Números do vazamento estrito, para comparação

`scripts/audit/leakage_strict.py`, detector em dois níveis (N1 = gold como token inteiro no
abstract; N2 = gold como prefixo de token maior, `KRAS`→`KRASG12D`), seguido de **revisão de
100% dos itens sinalizados** (censo, 17 itens únicos).

| | Acertos | Sinalizados | Confirmados | Taxa |
|---|---|---|---|---|
| Estritos | 3.865 | 51 (17 itens) | **36** | **0,9%** |
| Expandidos | 1.773 | 309 (127 questões) | **306** | **17,3%** |

Dos 17 itens: 12 vazamento real (36 acertos), 3 falso alarme (8 acertos, todos `HLA-C` casando
com o início de *"HLA class I"*), 2 duvidosos (7 acertos: `parkin` dentro de *parkinsonism*,
`HOXB` dentro de `HOXB6`). Falso alarme e duvidoso são **descartados**, critério conservador:
na dúvida não se acusa vazamento, o que subestima o desconto.

### Por que a assimetria faz sentido

O mascaramento remove exatamente a string que o PubTator anotou, que é o gold. Então o gold
quase nunca sobrevive (0,9%). O que sobrevive são as **outras formas de nomear o mesmo gene**,
e é por isso que o eco se concentra onde o modelo respondeu um **sinônimo**.

### O vazamento é conservador: trabalha CONTRA a hipótese

Em ambos os fenômenos a taxa cresce do Q1 para o Q4 (eco: 9,3% → 55,5%). Não é acaso: em genes
obscuros o autor precisa apresentar a nomenclatura (*"proteína tal (SIGLA)"*, *"miR-524-3p"*),
enquanto em genes famosos escreve `TP53` e segue adiante. **A estrutura da escrita científica
correlaciona vazamento com obscuridade.**

Consequência: o vazamento **inflava artificialmente a cauda longa**. Descontá-lo torna a queda
Q1→Q4 **mais acentuada**, não menos. O erro de medição estava **escondendo** o efeito que a
tese defende, o oposto de um viés de confirmação.

---

## 8. Limitações declaradas

**(a) A taxa medida é um limite inferior.** Os detectores só acham o termo grafado da mesma
forma (módulo a normalização grega/LaTeX). Variantes ortográficas não cobertas (hifenização,
espaçamento, grafia britânica) não são sinalizadas e nunca chegam ao revisor.

**(b) CORRIGIDO em 2026-07-20: duas definições de "acerto estrito" conviviam no código.**
O julgador oficial (`judge.py`) contava **3.865** acertos estritos; o auditor
(`leakage_strict.py`) recalculava por conta própria com `contains_token` e contava
**3.776**. A diferença de **89 casos** era inteiramente **normalização**: o julgador converte
letra grega e LaTeX, o `contains_token` cru não: `hCGbeta`↔`hCGβ`, `IRE1alpha`↔`IRE1$\alpha$`,
`Wnt3A.`↔`Wnt3a`, `p120 catenin`↔`p120-catenin`. Os 3.776 eram subconjunto próprio dos 3.865, e
**esses 89 acertos nunca eram varridos em busca de vazamento**.

O auditor agora consome `acerto_estrito` do julgamento em vez de recalcular. **O julgador é a
fonte única da verdade** para o que conta como acerto.

Efeito da correção: 46→**51** acertos sinalizados, 15→**17** itens únicos. Os 3 itens novos
foram revisados manualmente em 2026-07-20:

| pmid | Gold | Resposta | Veredito |
|---|---|---|---|
| 10898498 | `HLA-C.` (com ponto final) | `HLA-C` | falso alarme, *"HLA class I"*, mesmo padrão dos outros dois |
| 16757480 | `spermidine/spermine N(1)-acetyltransferase` | `…N1-acetyltransferase (SAT1)` | vazamento real, título traz o nome por extenso |
| 10340378 | `insulin receptor` | `insulin-receptor` | vazamento real, *"binds poorly to the insulin receptor (IR) beta subunit"* |

**A taxa não mudou: 34/3.776 e 36/3.865 são ambos 0,9%.** A correção não altera nenhuma
conclusão, só elimina uma inconsistência que a banca poderia apontar.

O caso `spermidine/spermine` é instrutivo: a máscara falhou porque o gabarito grafa `N(1)-` e o
abstract escreve `N1-`. É a mesma variação ortográfica da limitação (a), aparecendo agora do
lado do **mascaramento**, não do detector.

**(b2) Falso alarme de eco ≠ crédito indevido do NEBB.** São perguntas diferentes: o detector
de eco pergunta *"a string estava copiável?"*; o NEBB pergunta *"a resposta é sinônimo válido do
gabarito?"*. Dos 3 falsos alarmes de eco, **2 são acertos legítimos** que o detector sinalizou
por engano (gold `insulin-like growth factor-1` → `IGF-1`, GeneID 3479 nos dois; gold
`(pro)napsin B` → `pronapsin B`, GeneID 256236 nos dois). **1 é crédito indevido do julgador**:

| pmid 7683646 | |
|---|---|
| Gold | `IGF-I`, GeneID 3479 = **IGF1** |
| Resposta creditada | `IGF-II`, GeneID 3481 = **IGF2**, gene diferente |
| Por que foi creditado | `IGF` é alias legítimo de IGF1, e a busca por token o encontrou **dentro** de `IGF-II`, o hífen conta como fronteira |

O julgador **já tem** a checagem que pegaria isso (*"o número e a letra grega da resposta
precisam ser compatíveis com os do gabarito"*), mas ela tem duas lacunas: (i) está na regra de
**abreviação parentética**, que roda *depois* do bloco NEBB, e como o NEBB dá `return` ao casar,
a checagem nunca é alcançada; (ii) os regexes cobrem `\d+` e letras gregas, mas `IGF-I` / `IGF-II`
são **numerais romanos**.

**Impacto medido: 1 acerto em 1.773 (0,06%).** Varredura de todos os acertos expandidos onde um
alias de ≤4 caracteres casa antes de `hífen + sufixo` na resposta: 15 questões, das quais 14
estão corretas (o sufixo pertence ao próprio gabarito: `TIMP`→`TIMP-1`, `PAI`→`PAI-1`,
`PLC`→`PLC-zeta`, `hCG`→`hCG-beta`). **Não corrigido**: re-julgar os 16.000 casos e propagar o
número por todos os documentos moveria a acurácia em 0,06 p.p., abaixo do arredondamento
reportado.

**(b3) Resposta que é símbolo oficial de outro gene: 12 acertos (0,7%), 7 questões.** Quase
todos são **colisão de nomenclatura no próprio HGNC**, não erro do julgador: `MLN` é alias de
MRLN *e* símbolo da motilina; `BACH1` é alias de BRIP1 *e* símbolo de outro gene; `Prx` é
abreviação da família peroxiredoxina *e* símbolo da periaxina; `TPRKB` é alias legado de TP53RK
*e* símbolo oficial do GeneID 51002. O julgador seguiu o HGNC corretamente, a ambiguidade está
na base de referência. Mesmo fenômeno já declarado antes como "110 aliases pertencem a mais de
um gene".

**(c) O detector sinaliza, não decide.** O modo de falso alarme conhecido é o **termo curto**:
96 dos 309 flags (31%) casam por termo de ≤3 caracteres, e é aí que se concentra o único erro
encontrado. Por isso o relatório quebra os flags por comprimento do termo.

**(d) Colisões de alias no dicionário NEBB** (110 aliases pertencem a mais de um gene) e
**golds malformados** (~2,6%, artefatos da anotação do PubTator, como `'MDM2) and 4'`)
permanecem não corrigidos. Ambos causam falso **negativo** (subestimam a acurácia) e
penalizam todos os modelos igualmente.

---

## 9. Acurácia auditada

`python3 scripts/evaluate/audited_accuracy.py`

**Denominador: 3.864 casos.** Os **136 itens contaminados** (12 de vazamento estrito + 124 de
eco, sem sobreposição) são **anulados**: saem do benchmark para os 4 modelos, tenham acertado
ou não.

| Modelo | Q1 | Q2 | Q3 | Q4 | Global |
|---|---|---|---|---|---|
| Llama 3.3 70B | 61,2% | 46,1% | 30,2% | 11,8% | **37,3%** |
| Gemma 4 Denso 31B | 62,1% | 43,4% | 21,6% | 12,6% | **34,9%** |
| Qwen 3.6 35B | 59,0% | 39,6% | 23,5% | 12,2% | **33,6%** |
| Gemma 4 MoE 26B | 53,2% | 32,6% | 17,3% | 10,2% | **28,3%** |

### 9.1 Decisão 1: anular a questão, não penalizar o modelo

Uma pergunta cujo abstract expõe a resposta é **defeituosa por construção**: está quebrada
independentemente de quem acertou. Descontar o acerto mas manter a pergunta no denominador
penaliza o modelo **duas vezes**: perde o ponto *e* leva um erro por uma questão que não
deveria existir.

Remover o item **só para quem acertou** foi descartado: daria denominadores diferentes por
modelo e quebraria a comparabilidade entre eles.

### 9.2 Decisão 2: contaminação é semântica, não lexical

Um item conta como contaminado se expõe **alguma** forma de nomear o gene, independentemente da
grafia que o modelo emitiu.

A primeira versão descontava só os acertos cuja **string** casava no texto. Isso deixava de fora
37 acertos, em 23 questões, onde o modelo respondeu a mesma entidade com outra grafia. Inspeção
manual dos 37: **nenhum** caso de recuperação independente.

| O texto expõe | O modelo respondeu | Operação |
|---|---|---|
| `VGLUT2` | `SLC17A6` | traduziu para o símbolo oficial |
| `cyclophilin A` | `PPIA` | idem |
| `BHD` | `FLCN` | idem |
| `AT1` | `AGTR1` | idem |
| `Prx` | `PRDX6` | idem |
| `REST` | `RE1-silencing transcription factor` | expandiu a sigla |
| `GALT`, `APP`, `RANK` | nome por extenso | idem |
| `miRNA-575` | `miR-575` | convenção de prefixo |

Traduzir `VGLUT2` para `SLC17A6` **exige ter lido VGLUT2**. Não é memória paramétrica do
gene-alvo; é conhecimento de nomenclatura aplicado a um termo que estava na tela. A pergunta da
auditoria é se o modelo **podia** ter copiado, não se copiou literalmente, o casamento de
string era um proxy com falso negativo conhecido.

### 9.3 Robustez: as quatro combinações

`--convencao {semantica,estrita} --escopo {ambos,numerador}`, global do Llama 3.3 70B:

| | anular item (`ambos`) | descontar acerto (`numerador`) |
|---|---|---|
| **semântica** | **37,3%** ← oficial | 36,4% |
| estrita | 37,3% | 36,7% |

Amplitude **0,9 p.p.**, ranking idêntico nas quatro. O eixo `semântica`/`estrita` **some** no
escopo `ambos` (as duas dão 37,3%), porque anular o item já remove todos os acertos dele,
qualquer que seja a grafia. **Nenhuma conclusão depende da escolha.**

### 9.4 O que a auditoria faz com o gradiente

| Modelo | Bruto (4.000) | Auditado (3.864) | Δ |
|---|---|---|---|
| Gemma 4 Denso 31B | 47,9 p.p. | **49,5 p.p.** | +1,6 |
| Llama 3.3 70B | 47,2 p.p. | **49,4 p.p.** | +2,2 |
| Qwen 3.6 35B | 45,2 p.p. | **46,8 p.p.** | +1,6 |
| Gemma 4 MoE 26B | 41,1 p.p. | **43,0 p.p.** | +1,9 |

Efeito por estrato, média dos 4 modelos:

| Estrato | Bruta | Auditada | Queda |
|---|---|---|---|
| Q1 | 59,8% | 58,9% | −0,92 p.p. |
| Q2 | 42,4% | 40,4% | −1,93 p.p. |
| Q3 | 24,4% | 23,1% | −1,24 p.p. |
| **Q4** | 14,4% | 11,7% | **−2,71 p.p.** |

O Q4 perde **3× mais** que o Q1. O erro de medição **escondia** parte do efeito que a tese
defende, o oposto de viés de confirmação, demonstrado agora sobre a população inteira.

### Arquivos desta auditoria

| Arquivo | Conteúdo |
|---|---|
| `scripts/evaluate/audited_accuracy.py` | acurácia bruta e auditada, com conferência do desconto |
| `scripts/audit/echo_expanded.py` | detector de eco, definição no docstring, seed=31 |
| `scripts/audit/echo_review_summary.py` | pós-estratificação, IC de Wilson, sensibilidade |
| `scripts/audit/leakage_strict.py` | detector de vazamento estrito (N1/N2) |

### Dados: dois arquivos por fenômeno, mesma estrutura

| Arquivo | Unidade | Linhas | Quem escreve |
|---|---|---|---|
| `echo_expanded_flagged.csv` | acerto | 309 | script (regerado sempre) |
| `echo_expanded_verdicts.csv` | **questão** | **127** | script gera as linhas, **humano preenche o veredito** |
| `leakage_strict_flagged.csv` | acerto | 51 | script (regerado sempre) |
| `leakage_strict_verdicts.csv` | **item** | **17** | script gera as linhas, **humano preenche o veredito** |

Regras, iguais nos dois:

- O `*_sinalizados.csv` é saída bruta do detector, um registro por **acerto**. Nunca editar à mão.
- O `*_vereditos.csv` tem **todas** as unidades revisáveis, uma por linha, inclusive as que
  são vazamento/eco real. Reexecutar o script **preserva** os vereditos já escritos e **avisa**
  se apareceu unidade nova sem revisão.
- No eco, a coluna `na_amostra` marca as 60 sorteadas. **Fechar o censo é revisar também as
  linhas com `na_amostra=nao`**, não precisa de arquivo novo.

> Consolidado em 2026-07-20. Antes eram 6 arquivos com semânticas diferentes: o eco tinha
> `censo` + `amostra` + `vereditos` (a relação entre os três não era óbvia) e o estrito tinha
> um `vereditos` que listava **só as exceções**, deixando os 12 itens de vazamento real
> implícitos no `VEREDITO_PADRAO`, impossível auditar a revisão sem reexecutar o script.
