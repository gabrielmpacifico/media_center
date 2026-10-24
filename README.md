# Media Center

Aplicação pessoal local em Streamlit para acompanhar séries, animes, doramas e filmes, com dados em Markdown. O módulo de finanças está reservado para uma etapa futura.

## Requisitos e inicialização no Windows

- Python 3.10 ou superior
- Conexão com a internet para consultar o TVmaze quando você cadastrar ou atualizar uma série
- Não é necessária chave de API, conta IMDb ou credencial AWS

No PowerShell, na pasta `media_center`, instale/atualize as dependências e inicie:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

O Streamlit abrirá `http://localhost:8501`. Dados ficam em `data/series/`; o backup cria ZIPs em `backups/`.
Depois de atualizar as dependências, também é possível iniciar clicando duas vezes em `Iniciar Media Center.bat`.

## Catálogo de séries

O cadastro e a atualização usam a API pública do [TVmaze](https://www.tvmaze.com/api), sem chave. Cole o link IMDb: o app extrai o ID e procura a série correspondente no TVmaze. A consulta só ocorre quando você clica para cadastrar ou para salvar e atualizar. O app usa imagem, nota do TVmaze, ano, duração, gêneros, status e a lista de episódios lançados para validar a temporada/episódio e calcular os totais. Não grava detalhes dos episódios, apenas seus números por temporada como metadado para calcular progresso e recomendar o próximo episódio.

Se o ID IMDb não estiver associado ao título no TVmaze, a atualização tenta localizar o nome exato salvo na sua lista. Após sincronizar, o app guarda a lista de números de episódio por temporada como metadado interno nas anotações do Markdown, sem acrescentar campos ao frontmatter.

Cards com status `Assistindo` mostram o próximo episódio e oferecem **Marcar episódio como assistido**. Esse botão avança a posição e recalcula os pendentes usando os dados sincronizados. Quando a lista de episódios ainda não foi sincronizada, o app usa o próximo número sequencial até a próxima atualização.

O TVmaze é um catálogo de TV, então filmes e alguns títulos sem correspondência são cadastrados manualmente. Para isso, marque **Cadastrar sem consultar o TVmaze** e informe título e ano. Em filmes, a nota e a capa ficam vazias, e a atualização pelo catálogo é desativada. A nota de séries é a nota do TVmaze, não a nota IMDb.

Os dados do TVmaze são publicados sob CC BY-SA e exigem atribuição. O app atribui a fonte na página **Backup & Configurações**. A API limita chamadas por IP (pelo menos 20 chamadas a cada 10 segundos); uso normal manual fica abaixo desse limite. Veja a [documentação oficial da API](https://www.tvmaze.com/api) para detalhes de cobertura, limites e licença.

Quando o TVmaze informa que uma série terminou, o app só marca o status como `Finalizada` e desativa futuras atualizações se sua posição corresponder ao último episódio lançado. Caso contrário, preserva o status e mantém as atualizações disponíveis. Em séries ainda em exibição, se o status for `Assistindo` e a posição corresponder ao episódio mais recente lançado, muda para `Aguardando`. Se o TVmaze fornecer uma data para o próximo episódio, ela aparece nos detalhes e no card e é guardada no corpo de anotações (o frontmatter fixo não foi alterado). Ao concluir o último episódio de uma série encerrada, incrementa `vezes_completada`. Posições inválidas são recusadas e mostradas para correção.

O topo do catálogo mostra o total de episódios assistidos estimado a partir dos totais e pendências salvos, as horas estimadas, episódios pendentes, títulos e títulos com status `Assistindo`. A lista pode ser ordenada pelo número de episódios pendentes.

Após consulta ao TVmaze, os episódios pendentes são calculados com a quantidade real de episódios de cada temporada. Em títulos importados sem esse detalhamento, a estimativa distribui os episódios uniformemente entre as temporadas; horas assistidas e o indicador agregado de episódios vistos também são estimativas.

## Filmes

O TVmaze não oferece catálogo amplo de filmes. Ao escolher **Filme**, informe o link IMDb, o título e o ano; o cadastro é local e não faz chamada externa.

## Backup e arquivos

Em **Backup & Configurações**, clique em **Gerar Backup Zip**. Cada título fica em um `.md` individual, com os campos definidos no frontmatter; as anotações ficam sob `# Minhas Anotações`.

```text
media_center/
├── app.py
├── services/
│   ├── tvmaze_client.py
│   ├── progress.py
│   └── storage.py
├── data/series/
├── backups/
├── requirements.txt
└── README.md
```

## Carga inicial

A carga inicial foi importada de `Minha Lista de Séries e Animes.md`: 236 títulos, sem chamadas externas. As categorias, status, notas, durações, posição e totais informados na lista foram preservados. A lista não continha capas, por isso `foto_capa` começa vazio. Observações foram colocadas em `# Minhas Anotações`.
