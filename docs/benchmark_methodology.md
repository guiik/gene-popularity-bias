# Metodologia de Criação do Benchmark: Cálculo de Estratos e Mascaramento de Genes

> **Nota de repositório.** Este documento descreve a construção **original** do benchmark. Dois
> pontos foram superados desde então: o mascarador cobria apenas a primeira ocorrência do gene no
> abstract, e os estratos ficaram desbalanceados pela perda no filtro de anotação. O dataset
> publicado (`data/benchmark_v5.jsonl`) já usa o mascarador corrigido, que cobre todas as
> ocorrências, e os estratos rebalanceados em 1.000 casos cada. O código vigente está em
> [`scripts/build/mask_entities.py`](../scripts/build/mask_entities.py) e
> [`scripts/build/rebalance_strata.py`](../scripts/build/rebalance_strata.py).

Este documento consolida detalhadamente a metodologia utilizada para montar o dataset validado de avaliação de modelos de linguagem (geneturing), especificamente na identificação correta de genes humanos mascarados. Ele descreve de onde os dados vieram, como a importância de cada gene foi estratificada na literatura e como foram gerados os prompts de avaliação (cloze tasks).

## 1. Fonte de Dados e Escopo

A informação original que fundamenta a conectividade entre os Genes e a Literatura Científica foi extraída de arquivos públicos consolidados pelo NCBI (*National Center for Biotechnology Information*).
- **Arquivo de Referência:** `gene2pubmed.gz`
- **Origem:** Repositório FTP do NCBI (`ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2pubmed.gz`)

Esse arquivo mapeia em formato tabular quais artigos (presentes no banco de dados do PubMed identificados pelo `PubMed_ID`) mencionam de forma curada um determinado Gene (`GeneID`) pertencente a um organismo específico (`tax_id`).
Para este benchmark, o escopo de extração foi limitado **exclusivamente a genes humanos**, realizando um filtro severo nas linhas contendo a identificação `tax_id == 9606`.

## 2. Cálculo dos "Estratos" de Popularidade

A necessidade de se calcular um `estrato` surgiu para categorizar os genes humanos em diferentes "níveis de popularidade científica". Isso garante que o benchmark possa avaliar de maneira equilibrada se os modelos de IA funcionam bem apenas para genes famosos da literatura mainstream, ou se também dominam o conhecimento da periferia biomédica (a cauda longa).

A métrica base utilizada foi a quantificação absoluta de menções (*paper count*): contou-se em quantos trabalhos acadêmicos únicos cada `GeneID` humano figurou oficialmente no arquivo `gene2pubmed`. Em seguida, a distribuição dessas contagens originou quatro quadrantes rigorosos de agrupamento (os Estratos):

1. **Q1_Super_Populares** (>= 1.000 artigos):
   - Mapeia a "elite" da genética. Genes de massiva notoriedade e vastamente referenciados na fundação oncológica e biomédica global, contando com 1.000 ou mais publicações associadas cada.

2. **Q2_Medios** (De 100 a 999 artigos):
   - Genes de forte interesse científico e clinicamente fundamentados, mas que não atraem a hiper-concorrência bibliográfica do estrato principal.

3. **Q3_Baixa_Popularidade** (De 10 a 99 artigos):
   - Genes muitas vezes secundários, pertencentes a cascatas específicas, proteínas acessórias ou operantes em campos biológicos mais reclusos do mapeamento da fisiologia comum.

4. **Q4_Cauda_Longa** (< 10 artigos):
   - Os ilustres desconhecidos: novos genes descobertos nos últimos limiares geonômicos, seqüências pouco caracterizadas ou onde o interesse científico formal quase não produziu evidências primárias abrangendo múltiplos papers indíviduais na literatura biomédica.

## 3. Amostragem Balanceada

Ter acesso indiscriminado à listagem do NCBI geraria um gargalo de mensuração perigoso (já que existia um volume esmagador de evidências que distorcem o acervo focando nos genes Q1). Por isso, optou-se por realizar uma **Amostragem Estratificada Balanceada**, objetivando retirar **um target cravado de 1.000 papers para cada um dos quatro referidos estratos**.

Entretanto, se um único gene do topo (ex. TP53) tomasse conta de 800 do total de vagas de amostras do Q1, isso corromperia e invalidaria a amostrabilidade do volume geral do grupo, não captando a vasta gama de outros *Super Populares*. Para assegurar a densidade e o enriquecimento do ecossistema amostral de testagem:
- **Teto Imposto no Q1**: Permitiu-se retirar do grupo de opções aleatorizadas um máximo de **10 publicações** distintas ligadas ao mesmo Gene.
- **Teto Imposto ao Q2, Q3 e Q4**: Estipulou-se um encurtamento da presença dominante, liberando-se uma fatia de contribuição com máximo de **5 publicações** por Gene selecionado daquele estrato.

O dataframe de papéis foi desidratado retirando duplicatas do `PubMed_ID`, o `seed=42` foi evocado assegurando reprodutibilidade temporal na aleatorização para escolher as triagens, limitando as presenças com `groupby('GeneID').head()`. Depois de fechadas, estas instâncias (num ratio de 1000PMIDs/Estrato) definiram a bateria final de testes no arquivo cru consolidado de metadados listáveis `benchmark_pmids.csv`.

## 4. O Processo de Mascaramento (*Cloze Task Generation*)

De posse do agrupamento balanceado de PMIDs extraídos no estágio três, faltava resumi-los nos cadernos de respostas (prompts textuais). A pipeline de automação (*Script mask*.py) cuidou de buscar na literatura e fazer a colmatação (*Cloze Task*), executando os passos descritos sob demanda:

1. **Consulta Ouro na API do PubTator Central**:
   - Cada PubMed ID do banco base de testes passou a ser consultado individualmente via `https://www.ncbi.nlm.nih.gov/research/pubtator-api/publications/export/biocjson`.
   - O *PubTator* é um front de agregação essencialmente vantajoso da NIH e NCBI: Ele envia associadamente junto aos resumos (abstracts) de literatura, as etiquetas computadas das inferências (*Entities Annotations*). Sendo assim, sabe-se onde dentro da frase mora com certeza um tipo "Gene".

2. **Interceptação de Textos Exatos (Golden Matcher)**:
   - A triagem percorreu metodicamente as passagens devolvidas pelo PubTator para as localizações que o identificador de referência retornasse uma concordância estrita com o nosso `GeneID` já escolhido nos metadados (*no identifier annotation*).
   - Constatada a associação dentro do abstract, estrai-se formalmente as nomenclaturas e a escrita linguageira por trás. (ex: *"tumor protector factor p53"* etc. convertidas formalmente para `resposta_esperada` validando se o LLM conseguirá redigir a grafia da mesma forma da original que existia lá).

3. **Extirpação e Formação Oclusa (Prompt Mask injection)**:
   - Uma vez identificada a expressão que reflete o gene dentro da passagem completa, uma retração textual substitutiva suprime (*replace text*) a escrita real, encrustando em pleno texto do abstract e no seu lugar imediato do texto original um literal `[MASK]`.

4. **Escrita do Output Prompt (A Bateria do Prompt)**:
   - Envolvendo o corpo textual modificado, anexávamos a casca instrucional diretiva padrão que alimentará o modelo: *"Based on your knowledge of genomic literature, fill in the [MASK] with the correct gene symbol in the following abstract:\n\n"* seguido do *"Abstract Oculto"*.

Os extratos em união com o texto de chamada e as flags originais do quadrante compõem a grade em linhas unitárias formatdas do `data/benchmark_v5.jsonl`, entregando por fim todo o alicerce fundamental, equilibrado, destribulado e puramente isento de IDs numéricos e pronto para validação cega.
