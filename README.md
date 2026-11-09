# Beyond Nen Meditation Macro

**An automatic meditation macro for Beyond Nen (Roblox), featuring color-independent target detection and synchronized mouse input.**

[English](#english) | [Português](#português)

---

## English

An automatic meditation helper for **Beyond Nen (Roblox)**. It detects the meditation target and its closing ring, estimates the contact time, and sends a synchronized mouse click.

> Designed for Windows 10/11. Roblox must be open and in the foreground while automatic mode is running.

### Features

- Automatic meditation minigame detection and clicking.
- Detects the target using its stable white outline, so the target's fill color can change between meditation techniques.
- Measures the closing ring by visual contrast instead of relying on one fixed ring color.
- Predicts the ring-to-target contact time from recent measurements.
- Moves the cursor toward the target ahead of the click, then sends an ordered absolute mouse-move and left-button input through the Windows `SendInput` API.
- Live visual diagnostics without automatic clicking.
- Optional screen capture region, configurable by X, Y, width, and height.
- CSV activity log with detection samples, input events, and scheduling information.
- Global hotkeys to start, diagnose, test input, and stop the macro.

### Requirements

- Windows 10 or Windows 11.
- Python 3.10 or newer, available through the `py` launcher (recommended) or `python` command.
- Beyond Nen running in Roblox.
- The Roblox window must remain in the foreground while automatic mode is active.

### Installation and usage

1. Download the repository using **Code → Download ZIP**, then extract the folder.
2. Run `INICIAR_MEDITACAO.bat`.
3. On the first launch, the batch file installs the required Python packages automatically.
4. Open Beyond Nen in Roblox and keep the Roblox window in the foreground.
5. Press **F6** or click **INICIAR AUTO**.

You can also install the dependencies and run the application manually:

```bat
py -3 -m pip install -r requirements.txt
py -3 meditation_macro.py
```

### Controls

| Key | Action |
| --- | --- |
| **F6** | Start or arm automatic meditation clicking |
| **F8** | Start visual diagnostics without clicking |
| **F7** or **ESC** | Stop the macro immediately |
| **PAUSE/BREAK** | Send a test click at the current cursor position (Roblox must be in the foreground) |

The same actions are available through the application buttons.

### Settings

- **Capture region:** Set X and Y to the top-left screen coordinate and enter a positive width and height to capture only that region. Leave width and height at `0` to use the primary display.
- **Timing adjustment:** The default is `0 ms`. A positive value schedules the click earlier; a negative value schedules it later. The available range is `-150 ms` to `+120 ms`. Adjustments are read when automatic mode is armed with F6.
- **Visual diagnostics:** Enable the diagnostic preview to see the detected target, estimated ring gap, shrink speed, ETA, and detector timing. Diagnostic mode does not send automatic clicks.

### Logs and troubleshooting

- The application writes `meditation_macro_log.csv` next to the script. It records detection samples, cursor positioning, click-down/click-up events, and timing details.
- Keep Roblox in the foreground. If the game loses focus before a scheduled click, that click is cancelled.
- If Roblox is running with elevated permissions and does not receive input, close the macro and try `INICIAR_COMO_ADMIN.bat` as a separate test. Use administrator mode only when needed.
- Use **PAUSE/BREAK** with the cursor over a harmless in-game button to test mouse input independently from target detection.
- Antivirus software may inspect scripts that capture the screen or generate mouse input. Review the source code before running it and only use copies from a repository you trust.

### Notes

- The macro uses visual screen capture and Windows mouse input; it does not read or modify Roblox memory, inject code into the game, or access account credentials.
- The Windows API can confirm that input events were submitted, but only the game itself can confirm whether a click counted as a successful meditation hit.
- This repository contains source code and Windows launch scripts. It does not include a prebuilt executable.
- The code was syntax-checked before packaging. This does not replace testing on every Windows configuration or Roblox update.
- No license has been selected for this repository yet.

---

## Português

Um auxiliar de meditação automática para **Beyond Nen (Roblox)**. Ele identifica o alvo e o círculo que se fecha, calcula o momento aproximado do contato e envia um clique sincronizado.

> Desenvolvido para Windows 10/11. O Roblox precisa estar aberto e em primeiro plano enquanto o modo automático estiver ativo.

### Recursos

- Detecção e clique automáticos no minigame de meditação.
- Identificação do alvo pela borda branca estável, permitindo que a cor de preenchimento mude entre diferentes técnicas de meditação.
- Medição do círculo de aproximação pelo contraste visual, sem depender de uma única cor fixa.
- Previsão do momento de contato com base nas medições recentes.
- Movimento antecipado do cursor até o alvo e envio ordenado do movimento absoluto e do clique esquerdo pela API `SendInput` do Windows.
- Diagnóstico visual em tempo real, sem cliques automáticos.
- Região de captura opcional, configurável por X, Y, largura e altura.
- Registro CSV com amostras de detecção, eventos de entrada e informações de temporização.
- Atalhos globais para iniciar, diagnosticar, testar a entrada e parar o macro.

### Requisitos

- Windows 10 ou Windows 11.
- Python 3.10 ou superior, disponível pelo comando `py` (recomendado) ou `python`.
- Beyond Nen aberto no Roblox.
- A janela do Roblox deve permanecer em primeiro plano durante o modo automático.

### Instalação e execução

1. Baixe o repositório pelo botão **Code → Download ZIP** e extraia a pasta.
2. Execute `INICIAR_MEDITACAO.bat`.
3. Na primeira execução, o BAT instala automaticamente as bibliotecas necessárias.
4. Abra Beyond Nen no Roblox e deixe a janela do Roblox em primeiro plano.
5. Pressione **F6** ou clique em **INICIAR AUTO**.

Também é possível instalar manualmente as dependências e executar o aplicativo:

```bat
py -3 -m pip install -r requirements.txt
py -3 meditation_macro.py
```

### Controles

| Tecla | Ação |
| --- | --- |
| **F6** | Iniciar ou armar a meditação automática |
| **F8** | Abrir o diagnóstico visual sem clicar |
| **F7** ou **ESC** | Parar o macro imediatamente |
| **PAUSE/BREAK** | Enviar um clique de teste na posição atual do cursor (Roblox precisa estar em primeiro plano) |

As mesmas ações estão disponíveis pelos botões da interface.

### Configurações

- **Região de captura:** informe X e Y como coordenadas do canto superior esquerdo da região e preencha uma largura e altura positivas para analisar apenas essa área. Deixe largura e altura em `0` para usar a tela principal inteira.
- **Ajuste temporal:** o padrão é `0 ms`. Um valor positivo antecipa o clique; um valor negativo atrasa. O intervalo disponível é de `-150 ms` a `+120 ms`. O ajuste é lido ao armar o modo automático com F6.
- **Diagnóstico visual:** mostra o alvo detectado, a diferença estimada entre os círculos, a velocidade de fechamento, o tempo estimado até o contato e o tempo de processamento. O modo de diagnóstico não envia cliques automáticos.

### Logs e solução de problemas

- O aplicativo cria `meditation_macro_log.csv` na mesma pasta do script. O arquivo registra amostras de detecção, posicionamento do cursor, eventos de pressionar/soltar o botão e informações de temporização.
- Mantenha o Roblox em primeiro plano. Se o jogo perder o foco antes do clique agendado, a tentativa será cancelada.
- Se o Roblox estiver sendo executado com permissões elevadas e não receber a entrada, feche o macro e teste `INICIAR_COMO_ADMIN.bat` separadamente. Use o modo de administrador somente quando necessário.
- Use **PAUSE/BREAK** com o cursor sobre um botão inofensivo do jogo para testar o envio de cliques independentemente da detecção do alvo.
- Programas antivírus podem inspecionar scripts que capturam a tela ou enviam comandos de mouse. Revise o código antes de executar e utilize apenas cópias de um repositório em que confia.

### Observações

- O macro utiliza captura visual da tela e entrada de mouse do Windows; não lê nem modifica a memória do Roblox, não injeta código no jogo e não acessa credenciais da conta.
- A API do Windows pode confirmar que os eventos de entrada foram enviados, mas somente o próprio jogo pode confirmar se o clique foi contabilizado como acerto na meditação.
- Este repositório contém o código-fonte e os scripts de execução para Windows. Não inclui um executável pronto.
- A sintaxe do código foi verificada antes de gerar o pacote. Isso não substitui testes em todas as configurações do Windows ou atualizações do Roblox.
- Ainda não foi escolhida uma licença para este repositório.
