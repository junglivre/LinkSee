# LinkSee

LinkSee é uma ferramenta web simples para testar a prévia de links diretamente no navegador. Você cola uma URL pública e ele busca os metadados da página para montar uma prévia visual parecida com a usada por mensageiros, redes sociais e apps de chat.

Ele lê, quando disponíveis:

- `og:title`, `og:description`, `og:image`, `og:url` e outros campos Open Graph
- `twitter:title`, `twitter:description` e `twitter:image`
- `<title>`, `<meta name="description">`, canonical e favicon

Também mostra os metadados brutos encontrados para facilitar debug.

## Configuração

As principais opções ficam no topo do `server.py`:

```py
SERVER_PORT = 8787
PASSWORD_PROTECTED = 1
ACCESS_PASSWORD = "senha"
```

Use `PASSWORD_PROTECTED = 1` para exigir senha antes de acessar o sistema.

Use `PASSWORD_PROTECTED = 0` para deixar o sistema aberto, sem tela de login.

Antes de publicar em um subdomínio, troque `ACCESS_PASSWORD`.

Também dá para configurar sem editar o código: crie um arquivo `.env` na pasta do projeto (está no `.gitignore`):

```sh
SERVER_PORT=8787
PASSWORD_PROTECTED=1
ACCESS_PASSWORD=senha
```

Precedência: variável de ambiente > `.env` > default no `server.py`.

## Rodar

```sh
git clone https://github.com/junglivre/linksee
cd linksee
python3 server.py
```

Abra:

```text
http://127.0.0.1:8787
```

## Como usar

1. Cole uma URL pública no campo principal.
2. Clique em `Gerar prévia`.
3. Use `Atualizar sem cache` para refazer a busca enviando headers `Cache-Control: no-cache` e `Pragma: no-cache`.
4. Veja a prévia renderizada e os metadados extraídos ao lado.

## Deploy em subdomínio

O servidor escuta em `127.0.0.1:8787`, então o uso esperado em produção simples é colocar Nginx, Caddy ou outro proxy reverso na frente, com HTTPS.

Exemplo de proxy com Nginx:

```nginx
server {
    server_name linksee.seudominio.com;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Requisitos

- Python 3
- Acesso de rede do servidor para buscar as URLs testadas

Não precisa instalar dependências externas.

## Observações

- A ferramenta busca apenas URLs públicas `http` ou `https`.
- URLs locais, `localhost`, loopback e IPs privados são bloqueados por segurança.
- Alguns sites podem bloquear bots, exigir JavaScript ou retornar metadados diferentes dependendo do user agent.
- O limite de resposta HTML é de 2 MB para manter a ferramenta leve.
