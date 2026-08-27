# Auditoria do descarte pelo filtro do PubTator: por que um candidato não vira caso de teste

**Data:** 2026-07-14
**Artefatos:** `scripts/audit/pubtator_discard.py` · `results/audits/pubtator_discard.csv` · `results/cache_pubtator/`

---

## 0. A pergunta

Na construção do benchmark, nem todo candidato sorteado vira caso de teste: uma fração é
**descartada** pelo filtro de anotação do PubTator. A taxa de descarte cresce com a obscuridade do
gene (23,2% no Q1 → 63,8% no Q4), o que sugeriria um viés de
sobrevivência contra a cauda longa.

**Mas o número bruto não basta.** O descarte tem duas causas com significados opostos para a tese:

| Causa do descarte | Significado |
|---|---|
| **(a)** O gene simplesmente **não é nomeado no abstract** | **Neutro.** Não há lacuna a construir; nada se infere sobre o modelo. |
| **(b)** O gene **está no abstract**, mas o NER do PubTator não o reconheceu | **Soma na tese.** Remove sistematicamente os genes mais refratários à nomenclatura. |

Só a causa (b) introduz viés de dificuldade. Este documento mede **quanto** do descarte é (b),
com um método auditável caso a caso.

---

## 1. As três fontes de dados e seus papéis

A análise cruza três fontes independentes. É essencial não confundir seus papéis.

| Fonte | O que fornece | Papel na análise |
|---|---|---|
| **gene2pubmed** (NCBI, via `data/benchmark_pmids.csv`) | O par `(PMID, GeneID-alvo)`, "o artigo X é sobre o gene Y", por curadoria | **Verdade-base.** É daqui que vem o GeneID-alvo. |
| **PubTator3** (NCBI, API BioC) | As entidades que *ele* reconheceu no abstract (texto + anotações `Gene` com GeneID) | **O filtro que estamos auditando.** |
| **HGNC** (`nebb/data/hgnc_complete.json`) | Os nomes/aliases de cada GeneID | **Tradutor de nomes.** |

> **O GeneID-alvo NÃO vem do PubTator.** Vem do gene2pubmed. Se viesse do PubTator, a análise
> seria circular (o PubTator nunca discorda de si mesmo). Toda a análise é, no fundo, uma
> **discordância entre duas autoridades**: o gene2pubmed (curadoria, lê o texto completo) diz "o
> gene está neste artigo"; o PubTator (NER automático) diz "não o vejo no abstract". Quando as duas
> discordam **e** o gene está visível no abstract, temos a prova de que o reconhecedor falhou.

---

## 2. O algoritmo, passo a passo

Para cada candidato `(PMID, GeneID-alvo)`:

### Passo 1: buscar o artigo anotado
Baixa do PubTator o BioC JSON do PMID (com cache em `results/cache_pubtator/<pmid>.json`). Dele
extrai:
- **o texto** de todas as passagens (título + abstract);
- **as anotações `Gene`**, cada uma com seu `identifier` (o GeneID NCBI).

### Passo 2: sobreviveu ou foi descartado?
Junta o conjunto de GeneIDs que o PubTator anotou no artigo.
- **GeneID-alvo ∈ conjunto** → **sobreviveu** (o PubTator reconheceu o alvo; viraria caso de teste).
- **caso contrário** → **descartado**. Vai para o Passo 3.

### Passo 3: o gene está no texto, sob alguma forma?
Consulta as **formas do GeneID-alvo** no HGNC (ver Seção 3) e procura cada uma no texto:

| Situação | Classe | Significado |
|---|---|---|
| GeneID sem nenhuma forma no HGNC | **(u) não verificável** | obscuridade extrema, nem símbolo padrão tem |
| Alguma forma **aparece** no texto | **(b) no abstract, NER falhou** | **soma na tese** |
| Nenhuma forma aparece | **(a) fora do abstract** | neutro |

Cada decisão é gravada no CSV com a **forma que casou** e um **trecho de evidência**, de modo que
toda classificação (b) é verificável a olho.

---

## 3. Como funciona a consulta ao HGNC (o ponto que gera confusão)

**Não** se comparam "todos os nomes do HGNC" contra o texto, o que acharia qualquer gene mencionado
no abstract, o que é inútil. A busca é **direcionada**:

**Passo A (uma vez):** lê-se o HGNC local (45.019 registros) e monta-se um **índice**
`GeneID → formas daquele gene`. É uma agenda telefônica. Exemplo para o SRC (GeneID 6714):

```
6714 → {SRC, ASV, c-src, SRC1}      (symbol, alias_symbol, prev_symbol)
```

**Passo B (por candidato):** dado o GeneID-alvo, consulta-se **apenas o verbete dele**. Recebe-se
só os ~4–10 apelidos daquele gene. **Os outros 45.018 registros não são tocados.**

**Passo C (busca no texto):** para cada uma dessas poucas formas *do gene-alvo*, procura-se no
abstract:
- **símbolos** (`symbol`, `alias_symbol`, `prev_symbol`): casamento como **token inteiro**
  (cercado por não-alfanumérico), *case-insensitive*, comprimento ≥ 2. Assim `SRC` casa com "Src
  kinase", mas `MET` **não** casa dentro de "**met**abolism".
- **nomes por extenso** (`name`, `alias_name`, `prev_name`): *substring case-insensitive*, só para
  nomes com mais de 4 caracteres.

Basta uma forma casar para o gene ser considerado presente. Portanto o "palavra por palavra"
existe só no Passo C, e apenas entre **as formas de um único gene** e o texto, nunca entre o HGNC
inteiro e o texto. São ~4 a 10 buscas por candidato, não 45 mil.

---

## 4. Exemplo completo: o gene H3.Y (PMID 31722199)

| Etapa | O que acontece |
|---|---|
| gene2pubmed (CSV) | `PMID 31722199 → GeneID 391769 (H3.Y)`. **Este é o gabarito.** |
| PubTator | Anotou apenas o GeneID 100288687 (DUX4). O 391769 **não** está entre as anotações → **descartado**. |
| HGNC | Formas do 391769: `{H3.Y, H3.Y.1, H3Y1}`. |
| Busca no texto | O título diz "DUX4-Induced Histone Variants H3.X and **H3.Y** Mark DUX4 Target Genes". `H3.Y` casa. |
| **Veredito** | **(b)**, o gene estava no abstract; o NER é que falhou. Evidência gravada no CSV. |

É o caso-arquétipo do viés: uma variante de histona, nomenclatura recente, visível no título e
ainda assim invisível para o reconhecedor automático.

---

## 5. Resultado

Amostra reauditada: **150 candidatos do Q1 e 300 do Q4**, sorteados do conjunto balanceado original
(`seed=42`), re-checados ao vivo contra o PubTator.

**Validação do método:** as taxas de descarte reproduzem as documentadas para o filtro completo:
**22,0% no Q1 e 62,3% no Q4** na amostra, contra 23,2% e 63,8% documentados. Isso confirma que a
reauditoria reproduz o filtro real.

### Decomposição do destino dos candidatos (% do estrato)

| Destino | Q1 | Q4 |
|---|---|---|
| Sobreviveu (virou caso de teste) | 78,0% | 37,7% |
| Descarte **(a)**, gene não nomeado no abstract *(neutro)* | 10,7% | 32,0% |
| Descarte **(u)**, gene sem símbolo/nome padrão HGNC *(não verificável)* | 0,7% | 14,0% |
| **Descarte (b)**, gene no abstract, NER não reconheceu *(viés)* | **10,7%** | **16,3%** |

### Leitura

- **Boa parte do descarte é neutra.** Cerca de metade dos descartes do Q4 (a fração (a)) é apenas o
  gene não sendo nomeado no abstract, atrito de construção, não viés. O `gene2pubmed` liga
  gene↔artigo pelo **texto completo**, então o gene pode ser estudado sem aparecer no resumo.
- **O viés real (b) existe, porém é modesto:** 16,3% dos candidatos do Q4 contra 10,7% do Q1
  (razão ≈ **1,5×**). Sempre na direção conservadora.
- **A fração (u)**, genes sem nomenclatura HGNC padrão, é fortemente concentrada no Q4 (14,0% vs.
  0,7%). É marcador de obscuridade extrema; sua presença no texto não pôde ser verificada, mas sua
  exclusão também correlaciona com raridade.

**Conclusão:** o viés de sobrevivência é **real mas modesto**, longe de justificar a leitura
ingênua dos 62% de descarte bruto como se fossem todos viés. A maior parte do descarte é atrito de
construção.

---

## 6. Verificação manual e limitação

**Inspeção caso a caso.** Os **65 casos (b)** (16 no Q1, 49 no Q4) foram lidos um a um pela coluna
de evidência do CSV. Todos são o gene correto de fato presente no texto: lncRNAs nomeados
(`GASAL1`, `ELF3-AS1`, `SOX9-AS1`, `HOXC13-AS`…) e símbolos curtos legítimos (`MET`, `ATM`, `SRC`,
`APC`, `p62`). **Nenhum falso positivo aparente.**

**(b) é um LIMITE INFERIOR.** Se o abstract grafou o gene numa forma que o HGNC não lista como
alias, o caso cai em (a) por engano. Logo o viés real é **no mínimo** igual ao medido, nunca menor.
A limitação é conservadora, joga a favor da honestidade do argumento, não contra.

---

## 7. Reprodutibilidade

```bash
python3 scripts/audit/pubtator_discard.py            # amostra (seed=42): 150 Q1 + 300 Q4
python3 scripts/audit/pubtator_discard.py --full     # todos os candidatos de cada estrato
```

- **Cache:** as respostas brutas do PubTator ficam em `results/cache_pubtator/<pmid>.json`, e a
  reexecução é offline e o dado-fonte fica preservado para auditoria futura.
- **Saída:** `results/audits/pubtator_discard.csv`, uma linha por candidato, com colunas
  `estrato, pmid, geneid, hgnc_symbol, status, forma_casada, trecho_evidencia`.

---

## 8. Contexto conceitual (esclarecimentos que sustentam a análise)

Estes pontos foram levantados na discussão e valem registro, porque delimitam o que a análise pode
e não pode afirmar.

### 8.1 Papéis do gene2pubmed vs. PubTator

- **gene2pubmed** é base **curada**, no nível do artigo inteiro: liga gene↔publicação por curadoria.
- **PubTator** é ferramenta de **mineração de texto** (NER por aprendizado profundo, família
  AIONER/GNorm2 do NCBI) que **localiza e normaliza** menções no abstract.
- Não são um par projetado: são recursos independentes do NCBI que **nós** cruzamos (gene2pubmed
  como gabarito, PubTator como localizador). O casamento entre eles é sempre por **identificador
  numérico**, nunca por título ou DOI:

  | Cruzamento | Chave | Fonte A | Fonte B |
  |---|---|---|---|
  | mesmo **paper** | PMID | gene2pubmed | PubTator |
  | mesmo **gene** | GeneID | gene2pubmed | anotação do PubTator |

### 8.2 O gabarito é ancorado no GeneID, não na string mascarada

Cada caso tem duas âncoras: `resposta_esperada` (a string que o PubTator localizou e foi apagada,
ex. "brain natriuretic peptide") e `gene_id_gabarito` (o GeneID do gene2pubmed, ex. 4879 = NPPB).
**O julgador nota contra o GeneID, não contra a string.** Isso *evita* erro em vez de induzir:

- por string, "brain natriuretic peptide" resolveria para **NPPA** (o peptídeo *atrial*);
- por GeneID (4879), exige **NPPB** (o correto).

Foi a mesma classe de ambiguidade (`FAS`→FASN, `Rac1`→RNASE1) que quebrava o julgador antigo.
Erros residuais existem e são conservadores (falso negativo): colisões de alias (19/4.000 = 0,5%),
2,2% de GeneIDs fora do HGNC, e eventual erro de normalização do PubTator na construção.

### 8.3 Golden Matcher (construção) vs. NEBB (avaliação)

O **Golden Matcher** (`scripts/build/mask_entities.py`, função `mask_doc`) é o nome interno do algoritmo de
**mascaramento**. Antes de mascarar, confere se o GeneID que o PubTator anotou bate com o
GeneID-alvo do gene2pubmed; então mascara **todas** as ocorrências do gene (título + abstract),
inclusive a mesma palavra sob GeneID diferente (ortólogos/isoformas). O bug corrigido em v3 era
mascarar só a **primeira** ocorrência, deixando as demais vazarem o gabarito. O Golden Matcher só
enxerga o que o PubTator anotou, e daí o vazamento residual e o viés de sobrevivência.

### 8.4 Alternativa de mascaramento por dicionário (trabalho futuro)

A "heurística simples" (GeneID → aliases do HGNC → mascarar tudo por dicionário) é exatamente o
que esta auditoria usa para detectar a presença do gene. Para genes **obscuros**, ela é **melhor**
que o PubTator (acha o H3.Y que o NER perde), e reduziria **tanto** o vazamento residual **quanto**
o viés de sobrevivência. O custo é a desambiguação de nomes ambíguos de genes populares (`FAS`,
`MDR1`), onde o PubTator, por usar contexto, ganha. Trocar o mascaramento por dicionário exigiria
regerar o dataset e re-rodar os 4 modelos, e fica como direção futura.
