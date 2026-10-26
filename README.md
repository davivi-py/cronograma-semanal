# Quadro da semana

Um cronograma semanal de arrastar e soltar que eu uso para organizar estudo, rotina e descanso. Eu monto a semana em blocos de 30 minutos, das 07:00 à 01:30, e acompanho no dia a dia pelo computador ou pelo celular.

Fiz porque os apps de agenda que testei eram pesados demais para o que eu precisava: ver a semana inteira de uma vez, mover blocos rápido e ter alguns atalhos para dias difíceis (tipo o botão de "mínimo viável").

![Quadro da semana no computador](docs/quadro-desktop.png)

## Funcionalidades

- **Grade semanal** de segunda a domingo, com blocos coloridos por tipo (âncora fixa, estudo, sono, refeições…)
- **Arrastar e soltar** para criar blocos a partir da paleta e mover blocos entre dias e horários, com detecção de conflito
- **Redimensionar** blocos puxando a borda de baixo
- **Tipos personalizados** com nome e cor; a cor do texto é escolhida automaticamente pelo contraste
- **Contador de horas de estudo** da semana
- **Mínimo viável**: um botão que encaixa um bloco de 20 min no primeiro horário livre depois de agora
- **Zona de fricção**: bloco com temporizador que avisa com som a hora de parar
- **Duplicar semana** para a próxima, e **exportar/importar** tudo em JSON
- Dados salvos no navegador (`localStorage`), sem backend

## Versão sincronizada (`artifact/`)

Em `artifact/quadro-da-semana.html` fica a versão que eu uso no dia a dia. Ela roda como Artifact no claude.ai e tem tudo da versão original, mais:

**Uso em qualquer aparelho**
- **Sincroniza** entre celular e computador, usando o banco de documentos da própria plataforma
- **Pensada para o celular**: visão por dia, colocar blocos com toque, arrastar com toque longo e editar texto, dia, horário e duração numa janela
- **Agora / Depois**: mostra o bloco atual, o próximo e quanto tempo falta, com uma linha marcando a hora atual
- **Modo escuro** automático
- **Paletas de cores**: nove combinações prontas que recolorem todos os tipos de uma vez, e dá pra ajustar a cor de cada tipo

**Tracking dos blocos de estudo**
- Cada tipo de bloco pode ter uma **prioridade** (Graduação, DS/IC ou CodeLabs); só os blocos com prioridade entram no tracking
- Depois que um bloco passa, ele ganha um **?** e dá pra marcar como **feito** ou **não feito**, com um motivo opcional (cansaço, distração, imprevisto…)
- O botão **Registrar N blocos** junta numa lista tudo o que já passou e ainda não foi marcado
- O contador do topo mostra **horas feitas de horas planejadas** na semana
- O **resumo** mostra quanto do plano foi cumprido por prioridade, as últimas 4 semanas, o rendimento por turno, por dia e por tamanho de bloco, e os motivos mais comuns

<img src="docs/quadro-registro.png" alt="Lista para registrar os blocos que já passaram como feito ou não feito" width="300">

![Resumo do tracking com o plano cumprido por prioridade e as últimas semanas](docs/quadro-resumo.png)

**Entregas**
- Lista de trabalhos e tarefas com matéria e prazo, separada em atrasadas, próximos 7 dias e mais pra frente
- **Colar do Moodle**: cole o texto da página de tarefas e as entregas com vencimento são reconhecidas sozinhas
- As entregas aparecem como marcadores na grade, no dia e hora do prazo, e também quando você abre um bloco da matéria

**Correções em relação à versão original**
- a importação mescla as semanas em vez de apagar as outras;
- o "mínimo viável" depois da meia-noite entra na noite certa;
- remover um tipo não deixa blocos órfãos.

<img src="docs/quadro-celular.png" alt="Versão sincronizada no celular" width="300">

## Tecnologias

- HTML, CSS e JavaScript puros, sem frameworks, sem build e sem dependências
- Drag and drop nativo (HTML5) na versão original; Pointer Events na versão sincronizada, para funcionar com mouse e toque
- Web Audio API para o alarme do temporizador

## Como rodar

Não precisa instalar nada. Clone o repositório e abra o `index.html` no navegador:

```bash
git clone https://github.com/davivi-py/cronograma-semanal.git
cd cronograma-semanal
# abra index.html no navegador (duplo clique já funciona)
```

Para ver com dados de exemplo, clique em **Importar semana** e escolha o arquivo em `semanas-json/`.

## Estrutura

```
index.html                  página da versão original
app.js                      estado, grade, arrastar e soltar, timer, importar/exportar
styles.css                  estilos
artifact/
  quadro-da-semana.html     versão sincronizada: celular, tracking e entregas (página única)
semanas-json/               exemplo de semana exportada
docs/                       capturas de tela
```

## Formato dos dados

A exportação é um JSON com os tipos e os blocos de cada semana, identificada pela data da segunda-feira:

```json
{
  "version": 1,
  "weekStart": "2026-08-24",
  "types": [{ "id": "estudo", "name": "Estudo confiável", "color": "#0f766e" }],
  "weeks": {
    "2026-08-24": [
      { "id": "…", "typeId": "estudo", "day": 0, "slot": 18, "minutes": 120, "text": "Estudo confiável" }
    ]
  }
}
```

`day` vai de 0 (segunda) a 6 (domingo), e `slot` é o índice do horário de início em passos de 30 min a partir das 07:00.

Na versão sincronizada, o backup também leva as entregas (`tasks`), e cada bloco pode ter um registro de como foi:

```json
{ "id": "…", "typeId": "estudo", "day": 0, "slot": 18, "minutes": 120, "text": "Cálculo",
  "log": { "s": "pulei", "r": "imprevisto", "at": "2026-10-26T22:00:00.000Z" } }
```

`s` é `feito` ou `pulei` (não feito); `r` é o motivo, quando tem. Registros antigos podem ter `parcial` ou `troquei`.
