# recibos-bot

Bot de WhatsApp que registra as compras feitas no cartão da tia a partir de fotos de recibo e, no fim do
mês, devolve um resumo pronto para **você** encaminhar.

- Usa só a **WhatsApp Cloud API oficial** da Meta (nada de Baileys ou similares).
- Conversa **exclusivamente** com o `OWNER_PHONE`: ignora mensagens de outros números e o cliente HTTP
  recusa qualquer envio para outro destinatário (`RecipientNotAllowed`). Ele nunca manda nada para a tia.
- Extrai estabelecimento, valor, data e categoria com a API da Anthropic (visão + saída JSON com schema).
- Guarda tudo em SQLite, com dedupe por `message_id` e por `media_id` (a Meta às vezes reenvia o webhook).

## Como funciona

```
Você ──foto──▶ WhatsApp ──webhook POST──▶ Caddy (HTTPS) ──▶ uvicorn/FastAPI :8000
                                                              │ 1. confere X-Hub-Signature-256
                                                              │ 2. responde 200 na hora
                                                              │ 3. em segundo plano:
                                                              │    baixa a mídia (Media API)
                                                              │    extrai os dados (Anthropic)
                                                              │    salva no SQLite
Você ◀── "✅ Registrado #12: Extra - R$ 87,40 - 29/09 (Mercado)" ◀┘
```

### Uso no dia a dia

| Você manda | O bot faz |
|---|---|
| Foto ou PDF do recibo (legenda opcional, ex.: "parcelado 3x") | Registra e confirma: `✅ Registrado #12: Extra - R$ 87,40 - 29/09 (Mercado)` |
| **Responder** à confirmação com `valor 87,40`, `data 28/09`, `loja Extra`, `categoria farmácia` ou `apagar` | Corrige aquela compra. Dá pra mandar várias correções numa mensagem, separadas por `;` ou em linhas diferentes. Só `87,40` ou `28/09` também funciona |
| Correção em texto livre ("na verdade foi no Carrefour") | O modelo interpreta e aplica |
| Correção sem responder a nenhuma mensagem | Vale para a última compra, se ela tiver sido registrada nas últimas 24h |
| `/resumo` · `/resumo anterior` · `/resumo 09/2026` · `/resumo setembro` | Manda o resumo do mês numa mensagem separada, pronta para encaminhar |
| `/ultimas` | Últimas 10 compras com o número (#) |
| `/editar 12 valor 90,00` · `/apagar 12` | Corrige ou apaga pelo número |
| `/add Padaria Real 12,50 28/09 #lanche` | Registra sem foto (a data e a `#categoria` são opcionais) |
| `/ajuda` | Lista os comandos |

Exemplo de resumo:

```
*Compras no cartão – Setembro/2026*

02/09 · Extra · R$ 87,40
15/09 · Drogasil · R$ 30,01
29/09 · Posto Shell · R$ 150,00 (parcelado em 3x de R$ 50,00)

*Total: R$ 267,41* (3 compras)
Metade (50%): R$ 133,71
```

O mês do resumo é o da **data da compra** (não o do fechamento da fatura). A linha "Metade (50%)" pode ser
desligada com `SUMMARY_SHOW_SPLIT=false`.

Todo dia 1, às 09:00 (horário de Brasília), um timer do systemd manda o resumo do mês anterior para você.

> **Janela de 24h da Meta:** fora de uma conversa aberta nas últimas 24h, a Cloud API só deixa o bot mandar
> **templates aprovados**. Se você não falou com o bot nas 24h antes do dia 1, o envio de texto falha. Para
> isso, configure o template do passo 6 (`SUMMARY_TEMPLATE_NAME`). O bot manda o aviso "seu resumo de
> Setembro/2026 está pronto", você responde `/resumo anterior` e recebe o resumo. Sem template, basta mandar
> `/resumo anterior` quando quiser.

---

## Estrutura

```
recibos-bot/
├── app/
│   ├── main.py        # FastAPI: GET /webhook (verificação) e POST /webhook (assinatura + eventos)
│   ├── bot.py         # roteamento de mensagens, registro, correções, comandos, resumo agendado
│   ├── extractor.py   # chamadas à API da Anthropic (recibo -> JSON; correção em texto livre -> JSON)
│   ├── whatsapp.py    # cliente da Cloud API (envio, Media API, verificação de assinatura, trava de destinatário)
│   ├── db.py          # SQLite (schema e consultas)
│   ├── parsing.py     # valores em R$, datas, meses, categorias e correções "chave valor"
│   ├── summary.py     # formatação do resumo
│   ├── config.py      # leitura do .env
│   └── cli.py         # `python -m app.cli resumo ...` (usado pelo timer)
├── deploy/            # units do systemd e Caddyfile
├── tests/
├── .env.example
└── requirements.txt
```

### Schema do SQLite

```sql
purchases(
  id, purchase_date TEXT 'YYYY-MM-DD', merchant TEXT, total_cents INTEGER NULL,
  category TEXT, media_id TEXT UNIQUE, source_message_id TEXT, confirm_message_id TEXT,
  notes TEXT, created_at, updated_at, deleted_at NULL      -- apagar é soft delete
)
processed_messages(message_id PRIMARY KEY, received_at)    -- dedupe de reenvios do webhook
kv(key PRIMARY KEY, value)                                  -- wa_id do dono, resumos já enviados
```

Valores ficam em **centavos** (inteiro) para evitar erro de arredondamento.

---

## Deploy na Oracle Cloud (Ubuntu), passo a passo

Os comandos abaixo supõem Ubuntu 22.04 ou 24.04, acesso root via SSH e um domínio (ou subdomínio) apontando
para o IP público da instância.

### 0. Domínio → IP

A Meta exige um webhook em **HTTPS com certificado válido**. Escolhi **Caddy + Let's Encrypt**: é um binário só,
emite e renova o certificado sozinho e não depende de nenhum serviço de terceiros no caminho. Como a instância
já tem IP público, não há motivo para usar túnel.

- Com domínio próprio: crie um registro **A** `recibos.seudominio.com.br → <IP público>`.
- Sem domínio: crie um subdomínio grátis no [DuckDNS](https://www.duckdns.org) (`algo.duckdns.org`) apontando
  para o IP.

Confira com `dig +short recibos.seudominio.com.br` antes de seguir.

> Alternativa: Cloudflare Tunnel (`cloudflared`). Não precisa abrir as portas 80/443, mas exige o domínio na
> Cloudflare e põe mais um serviço no caminho. Se preferir, aponte o túnel para `http://127.0.0.1:8000` e pule
> os passos 1 e 4.

### 1. Liberar as portas 80 e 443 (duas camadas na Oracle)

**a) No painel da OCI:** Networking → Virtual Cloud Networks → sua VCN → Security Lists → Default Security
List → *Add Ingress Rules*: Source CIDR `0.0.0.0/0`, TCP, destination port `80`. Repita para `443`.
(Se a VNIC usa Network Security Group, adicione as regras no NSG.)

**b) No firewall da instância:** as imagens Ubuntu da Oracle vêm com regras de iptables que rejeitam tudo
exceto SSH.

```bash
sudo iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -m conntrack --ctstate NEW -j ACCEPT
sudo apt-get install -y iptables-persistent   # responda "yes" para salvar as regras atuais
sudo netfilter-persistent save
```

### 2. Código, usuário e venv

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv git sqlite3
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin recibos

sudo git clone https://github.com/davivi-py/cronograma-semanal.git /opt/cronograma-semanal
sudo ln -s /opt/cronograma-semanal/recibos-bot /opt/recibos-bot

sudo python3 -m venv /opt/recibos-bot/.venv
sudo /opt/recibos-bot/.venv/bin/pip install -r /opt/recibos-bot/requirements.txt
```

É preciso Python 3.10 ou mais novo (o Ubuntu 22.04 traz o 3.10 e o 24.04 traz o 3.12).

### 3. Variáveis de ambiente

```bash
sudo cp /opt/recibos-bot/.env.example /etc/recibos-bot.env
sudo chmod 600 /etc/recibos-bot.env && sudo chown root:root /etc/recibos-bot.env
sudo nano /etc/recibos-bot.env
```

Preencha tudo; os comentários do arquivo explicam onde achar cada valor. O systemd lê o arquivo como root,
então o `chmod 600` não atrapalha o serviço.

**Gerando o verify token** (é uma senha que você mesmo inventa; o mesmo valor vai no painel da Meta no passo 5):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Cole o resultado em `WHATSAPP_VERIFY_TOKEN`.

### 4. HTTPS com Caddy

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

sudo cp /opt/recibos-bot/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/recibos.seudominio.com.br/SEU.DOMINIO.AQUI/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

O Caddy só expõe `/webhook` e `/healthz`; qualquer outro caminho responde 404. O uvicorn escuta apenas em
`127.0.0.1:8000`.

### 5. Subir o serviço (systemd)

```bash
sudo cp /opt/recibos-bot/deploy/recibos-bot.service /etc/systemd/system/
sudo cp /opt/recibos-bot/deploy/recibos-resumo.service /etc/systemd/system/
sudo cp /opt/recibos-bot/deploy/recibos-resumo.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now recibos-bot.service
sudo systemctl enable --now recibos-resumo.timer

systemctl status recibos-bot
curl -s https://SEU.DOMINIO.AQUI/healthz            # {"ok":true}
systemctl list-timers recibos-resumo.timer            # próxima execução
```

Logs: `journalctl -u recibos-bot -f`.

### 6. Registrar o webhook no painel da Meta

O servidor precisa estar no ar (passo 5), porque a Meta testa a URL na hora.

1. Em [developers.facebook.com](https://developers.facebook.com/apps) → seu app → **WhatsApp → Configuration**
   (em alguns painéis fica em *Use cases → Connect with customers through WhatsApp → Customize → Configuration*).
2. Em **Webhook**, clique em **Edit**:
   - **Callback URL:** `https://SEU.DOMINIO.AQUI/webhook`
   - **Verify token:** o mesmo valor de `WHATSAPP_VERIFY_TOKEN`
   - **Verify and save.** A Meta faz um `GET /webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...`
     e o servidor devolve o `challenge` se o token bater. Se falhar, veja `journalctl -u recibos-bot`.
3. Em **Webhook fields**, clique em **Manage** e assine o campo **`messages`**.
4. Confirme que o app está inscrito na sua WABA. Esse passo costuma ser necessário quando o token é de System
   User:
   ```bash
   curl -X POST "https://graph.facebook.com/v23.0/<WABA_ID>/subscribed_apps" \
     -H "Authorization: Bearer $WHATSAPP_TOKEN"
   # conferir:
   curl "https://graph.facebook.com/v23.0/<WABA_ID>/subscribed_apps" -H "Authorization: Bearer $WHATSAPP_TOKEN"
   ```
5. **App Secret:** App settings → Basic → *App secret* → Show. Copie para `WHATSAPP_APP_SECRET`. É com ele que
   o servidor valida o `X-Hub-Signature-256` de cada POST; se não bater, o POST é descartado com 401.
6. Se os eventos de mensagens reais não chegarem, verifique se o app está em modo **Live** (publicado) e não em
   *Development*.

Depois de mudar o `.env`: `sudo systemctl restart recibos-bot`.

### 7. (Opcional, recomendado) Template para o resumo automático

No **WhatsApp Manager → Message templates → Create template**:

- Categoria: **Utility**
- Nome: `resumo_pronto`
- Idioma: **Portuguese (BR)** (`pt_BR`)
- Corpo: `Seu resumo de compras de {{1}} está pronto. Responda "/resumo anterior" para recebê-lo.`
- Exemplo para `{{1}}`: `Setembro/2026`

Quando for aprovado, coloque `SUMMARY_TEMPLATE_NAME=resumo_pronto` no `.env` e reinicie o serviço.

### 8. Primeiro teste

1. Do **seu** WhatsApp (o `OWNER_PHONE`), mande `/ajuda` para o número Business.
2. Mande a foto de um recibo e confira a confirmação.
3. Mande `/resumo` para ver o formato.
4. Para testar o envio agendado sem esperar o dia 1:
   ```bash
   # só imprime no terminal (mesmo usuário e .env do serviço):
   sudo systemd-run --pipe --wait -p User=recibos -p EnvironmentFile=/etc/recibos-bot.env \
     -p WorkingDirectory=/opt/recibos-bot /opt/recibos-bot/.venv/bin/python -m app.cli resumo --mes-anterior
   # envia de verdade (só uma vez por mês; para repetir, rode o comando acima com "--enviar --force"):
   sudo systemctl start recibos-resumo.service
   ```

> **Números brasileiros e o 9º dígito:** o `wa_id` que a Meta manda no webhook às vezes vem sem o 9
> (`55 11 8765-4321`). O bot aceita o `OWNER_PHONE` com ou sem o 9 e, para o envio agendado, usa o `wa_id`
> exato da última mensagem que você mandou.

---

## Manutenção

- **Atualizar:** `cd /opt/cronograma-semanal && sudo git pull && sudo /opt/recibos-bot/.venv/bin/pip install -r recibos-bot/requirements.txt && sudo systemctl restart recibos-bot`
- **Backup do banco** (cópia consistente mesmo com o serviço rodando):
  `sudo sqlite3 /var/lib/recibos-bot/recibos.db ".backup '/root/recibos-$(date +%F).db'"`
- **Consultar direto:** `sudo sqlite3 /var/lib/recibos-bot/recibos.db "select * from purchases order by id desc limit 10;"`
- **Token da Meta:** use token de System User, que não expira. Tokens temporários do *API Setup* duram 24h.
- **Graph API:** quando a Meta descontinuar a versão configurada, atualize `GRAPH_API_VERSION`.

## Desenvolvimento local

```bash
cd recibos-bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
cp .env.example .env   # ajuste DB_PATH=data/recibos.db
uvicorn app.main:app --reload
```

Os testes não chamam a Meta nem a Anthropic (usam dublês). Eles cobrem a verificação do GET, a assinatura dos
POSTs, o dedupe, a trava de destinatário, as correções, os comandos e o resumo.

## Custos e privacidade

- Cada recibo gera uma chamada à API da Anthropic com a imagem. Correções no formato `chave valor` não chamam
  o modelo; só as correções em texto livre chamam.
- Mensagens iniciadas por você abrem uma janela de atendimento de 24h. Respostas dentro dela não são cobradas
  pela Meta; o template do dia 1 (categoria *utility*) pode ser cobrado conforme a tabela da Meta.
- As imagens não são guardadas no servidor: são baixadas, enviadas para extração e descartadas. Só os dados
  extraídos vão para o SQLite.
