# Among Us 3D VR — частный сервер

Свой сервер вместо облака Photon для Among Us 3D VR на Meta Quest. С ним можно играть компанией в своей сети, например через NetBird, не завися от официальных серверов матчмейкинга.

Игра не меняется: никакого патча APK, root и сторонних модов. У каждого игрока остаётся своя лицензионная копия, в репозитории нет ни файлов игры, ни её кода.

## Как это работает

Игра ищет облако Photon по адресу `ns.photonengine.io`. Если этот домен у игрока указывает на наш сервер, игра подключается к нему так же, как к настоящему облаку.

```
Шлем игрока
  │  ns.photonengine.io → адрес нашего сервера (DNS)
  ▼
Name Server  (UDP 27000)  вход, выдаёт адрес Master
  ▼
Master       (UDP 27001)  находит комнату по коду, выдаёт адрес Game
  ▼
Game         (UDP 27002)  комната: пересылает пакеты между игроками
```

Для голосового чата есть такая же тройка серверов на портах `5058`, `5055` и `5056`.

**Кто что делает:**
- **Хост**, шлем того, кто создал лобби, ведёт всю игру: позиции, роли, раунды.
- **Сервер** не считает игру. Он пускает игроков, держит комнаты и пересылает пакеты между клиентами и хостом.
- **FusionPlugin** — небольшая часть сервера, которая заменяет закрытый плагин Photon Fusion: подтверждает вход в сессию, обменивает адреса игроков и подтверждает снимки состояния игры.

Вход в игру по-прежнему идёт через Oculus и Epic: игра сама получает свой токен. Сервер этот токен не проверяет и пускает всех, кто до него дошёл, поэтому доступ ограничивается сетью.

## Что нужно

**Серверу:**
- Linux или Windows 10/11. На Linux нужен Docker или Python 3.12+, на Windows Python 3.13+.
- Около 30 МБ памяти и 1–2 % одного ядра. Трафик примерно 0,1 Мбит/с в каждую сторону на игрока.
- UDP-порты `27000`, `27001`, `27002`, `5058`, `5055`, `5056`, открытые только для вашей сети, не для всего интернета.
- Компьютер должен быть включён всё время игры. Если он уснёт, лобби закроются.

**Игрокам:** на шлеме `ns.photonengine.io` должен резолвиться в адрес сервера. Больше ничего менять не нужно.

## Настройка NetBird

Это делается один раз в панели NetBird и не зависит от системы сервера.

1. Заведите DNS-запись `ns.photonengine.io → адрес сервера`. Адрес появится, когда сервер подключится к NetBird.
2. Создайте правило доступа между шлемами и сервером с протоколом `ALL`. С правилом только для ICMP пинг идёт, а игра висит на загрузке.

Подойдёт и любой другой способ, лишь бы шлем получал наш адрес для этого домена.

В примерах ниже `100.64.0.10` — условный адрес сервера, подставьте свой.

## Установка на Linux

Команды из этого раздела работают только на Linux. Для Windows есть [свой раздел](#установка-на-windows).

### 1. Подключите сервер к NetBird

Пропустите этот шаг, если сервер уже в сети. Ключ `<SETUP_KEY>` создаётся в панели NetBird.

```bash
curl -fsSL https://pkgs.netbird.io/install.sh | sh
sudo netbird up --setup-key <SETUP_KEY>
```

Адрес сервера в сети покажет команда:

```bash
netbird status | grep "NetBird IP"
```

### 2. Запустите сервер

Подойдёт любой из двух вариантов. Docker проще обновлять, systemd не требует Docker.

#### Вариант A: Docker

Если Docker ещё не установлен:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Сервер:

```bash
git clone https://github.com/aaaSaZaN/amongus-server.git
cd amongus-server
echo PUBLIC_ADDRESS=100.64.0.10 > .env
sudo docker compose up -d --build
```

Лог: `sudo docker compose logs -f`.

Обновление:

```bash
git pull
sudo docker compose up -d --build
```

#### Вариант B: systemd

На Debian и Ubuntu:

```bash
sudo apt install -y git python3-venv
```

Сервер:

```bash
sudo git clone https://github.com/aaaSaZaN/amongus-server.git /opt/amongus-photon
sudo python3 -m venv /opt/amongus-photon/.venv
sudo /opt/amongus-photon/.venv/bin/pip install -r /opt/amongus-photon/requirements.txt
echo PUBLIC_ADDRESS=100.64.0.10 | sudo tee /etc/amongus-photon.env
sudo cp /opt/amongus-photon/amongus-photon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now amongus-photon
```

Лог: `journalctl -u amongus-photon -f`.

Обновление:

```bash
sudo git -C /opt/amongus-photon pull
sudo systemctl restart amongus-photon
```

При обновлении и перезапуске текущие лобби закрываются, поэтому делайте это между партиями.

### 3. Проверьте

```bash
curl -s http://127.0.0.1:8080/api/status
```

Работающий сервер ответит примерно так:

```json
{"uptime": 12, "players": 0, "rooms": []}
```

Затем создайте лобби в игре: оно появится в ответе и на дашборде.

### 4. Закройте порты

Если на сервере есть файрвол, откройте порты только на интерфейсе NetBird, обычно это `wt0`. Для `ufw`:

```bash
sudo ufw allow in on wt0 to any port 27000:27002 proto udp
sudo ufw allow in on wt0 to any port 5055:5056 proto udp
sudo ufw allow in on wt0 to any port 5058 proto udp
```

Если провайдер даёт внешний файрвол, эти порты в нём открывать не нужно: трафик NetBird идёт через свой туннель.

## Установка на Windows

На Windows сервер работает как обычная программа на Python и запускается через Планировщик заданий. Docker здесь не используется: `compose.yaml` рассчитан на сетевой режим `host`, который в Docker Desktop выключен по умолчанию.

Все команды выполняйте в PowerShell, запущенном от имени администратора. Команды с `curl ... | sh` из раздела про Linux здесь не работают.

### 1. Подключите сервер к NetBird

Скачайте и запустите [установщик NetBird](https://pkgs.netbird.io/windows/x64). После установки откройте новое окно PowerShell, чтобы появилась команда `netbird`, и подключитесь. Ключ `<SETUP_KEY>` создаётся в панели NetBird.

```powershell
netbird up --setup-key <SETUP_KEY>
```

Адрес сервера в сети покажет команда:

```powershell
netbird status | Select-String "NetBird IP"
```

### 2. Установите Python и Git

```powershell
winget install --id Python.Python.3.13
winget install --id Git.Git
```

Если `winget` нет, скачайте установщики с [python.org](https://www.python.org/downloads/windows/) и [git-scm.com](https://git-scm.com/download/win). После установки снова откройте новое окно PowerShell.

Нужен именно Python 3.13 или новее: в старых версиях часы на Windows имеют шаг 15,6 мс, и пинг на дашборде будет неточным.

### 3. Скачайте сервер

```powershell
git clone https://github.com/aaaSaZaN/amongus-server.git C:\amongus-server
cd C:\amongus-server
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Set-Content -Path .env -Encoding ascii -Value "PUBLIC_ADDRESS=100.64.0.10", "LOG_FILE=server.log"
```

Файл `.env` создавайте этой командой или в Блокноте. Обычный `echo ... > .env` в Windows PowerShell запишет его в UTF-16, и сервер не сможет его прочитать.

### 4. Откройте порты

Файрвол Windows включён по умолчанию, поэтому без этого шага шлемы до сервера не дойдут. Правило пропускает только адреса сети NetBird:

```powershell
New-NetFirewallRule -DisplayName "Among Us VR server" -Direction Inbound -Action Allow -Protocol UDP -LocalPort "27000-27002", "5055", "5056", "5058" -RemoteAddress "100.64.0.0/10" -Profile Any
```

### 5. Включите автозапуск

Сервер будет запускаться вместе с Windows, даже если никто не вошёл в систему:

```powershell
$action = New-ScheduledTaskAction -Execute "C:\amongus-server\.venv\Scripts\python.exe" -Argument "-u photon.py" -WorkingDirectory "C:\amongus-server"
$trigger = New-ScheduledTaskTrigger -AtStartup
$options = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "AmongUsPhoton" -Action $action -Trigger $trigger -Settings $options -User "SYSTEM" -RunLevel Highest
Start-ScheduledTask -TaskName "AmongUsPhoton"
```

Чтобы компьютер не засыпал от сети:

```powershell
powercfg /change standby-timeout-ac 0
```

### 6. Проверьте

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/status
```

Сервер должен ответить полями `uptime`, `players` и `rooms`. Затем создайте лобби в игре: оно появится на дашборде по адресу `http://127.0.0.1:8080`.

Лог: `Get-Content C:\amongus-server\server.log -Tail 20 -Wait`.

Перезапуск:

```powershell
Stop-ScheduledTask -TaskName "AmongUsPhoton"
Start-ScheduledTask -TaskName "AmongUsPhoton"
```

Обновление:

```powershell
cd C:\amongus-server
git pull
.venv\Scripts\pip install -r requirements.txt
Stop-ScheduledTask -TaskName "AmongUsPhoton"
Start-ScheduledTask -TaskName "AmongUsPhoton"
```

При обновлении и перезапуске текущие лобби закрываются, поэтому делайте это между партиями.

## Дашборд

Страница показывает, сколько игроков на сервере, какие лобби открыты и какой пинг до каждого шлема. Она встроена в сервер и не требует баз данных и сторонних библиотек. Данные собираются только в момент запроса, а вкладка в фоне запросы не шлёт, поэтому на игру страница не влияет.

Сервер включает её сам, но только на `127.0.0.1:8080`, то есть извне она закрыта.

**Открыть с другого компьютера через SSH-туннель (Linux):**

```bash
ssh -L 8080:127.0.0.1:8080 user@100.64.0.10
```

Пока туннель открыт, дашборд доступен на `http://127.0.0.1:8080`.

**Открыть всем в сети NetBird.** Страница без пароля и показывает адреса игроков, поэтому публикуйте её только на адресе NetBird:

```bash
echo DASHBOARD=100.64.0.10:8080 >> .env
sudo docker compose up -d
```

Для systemd ту же строку добавьте в `/etc/amongus-photon.env` и выполните `sudo systemctl restart amongus-photon`. Если на сервере есть файрвол, откройте порт: `sudo ufw allow in on wt0 to any port 8080 proto tcp`.

На Windows:

```powershell
cd C:\amongus-server
Add-Content -Path .env -Encoding ascii -Value "DASHBOARD=100.64.0.10:8080"
New-NetFirewallRule -DisplayName "Among Us VR dashboard" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -RemoteAddress "100.64.0.0/10" -Profile Any
Stop-ScheduledTask -TaskName "AmongUsPhoton"
Start-ScheduledTask -TaskName "AmongUsPhoton"
```

Адрес можно указать до того, как поднимется NetBird: сервер будет пробовать занять его каждые 5 секунд, а игра тем временем уже работает.

**Что на странице:**

| Поле | Значение |
|---|---|
| Адрес | IP шлема. Игра не передаёт серверу имена игроков, поэтому различать их можно только по адресу. |
| Роль | `хост` ведёт игру, `игрок` подключён к нему. |
| Пинг | Сглаженное время туда-обратно между сервером и шлемом. До 60 мс — хороший, до 120 мс — средний, выше — высокий. |
| Голос | Подключён ли шлем к голосовой комнате лобби. |
| В лобби | Сколько времени шлем находится в комнате. |

Пинг показывает связь шлема с сервером, а не с другими игроками. В него входит и задержка самого клиента, поэтому у близких шлемов цифра может быть на несколько миллисекунд выше настоящей. Статус `нет связи` появляется, если от шлема нет пакетов 5 секунд. Через `IDLE_TIMEOUT` секунд тишины, по умолчанию 60, сервер отключает игрока.

Те же данные в JSON отдаёт `/api/status`, их можно забирать скриптом.

## Настройки

Настройки задаются в файле `.env` в папке проекта, для systemd в `/etc/amongus-photon.env`. После изменения перезапустите сервер: `sudo docker compose up -d`, `sudo systemctl restart amongus-photon` или команды перезапуска из раздела про Windows.

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PUBLIC_ADDRESS` | нет, обязательная | Адрес сервера, который получают шлемы. |
| `DASHBOARD` | `127.0.0.1:8080` | Где открыть дашборд. |
| `PING_INTERVAL` | `1.1` | Как часто сервер пингует шлемы в комнате, в секундах. `0` отключает пинг: дашборд будет обновлять цифру только при редких надёжных пакетах. |
| `IDLE_TIMEOUT` | `60` | Через сколько секунд тишины сервер отключает игрока. |
| `LOG_FILE` | нет | Файл для лога. Без него лог идёт в консоль. |

`IDLE_TIMEOUT` больше 20 секунд действует не всегда: если шлем молчит и не подтверждает игровой пакет, сервер отключит его через 20 секунд.

Переменные окружения важнее файла `.env`, а параметры командной строки важнее их обоих.

## Запуск вручную

Для отладки сервер можно запустить напрямую. На Windows вместо `.venv/bin/` используйте `.venv\Scripts\`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python photon.py --public 100.64.0.10
```

### Параметры `photon.py`

| Параметр | Что делает |
|---|---|
| `--public` | Адрес, который сервер выдаёт клиентам. Обязательный, если не задан `PUBLIC_ADDRESS`. Должен совпадать с адресом в DNS-записи. |
| `--dashboard` | Где открыть дашборд, в формате `адрес:порт`. По умолчанию `DASHBOARD` или `127.0.0.1:8080`. |
| `--host` | Где слушать порты, по умолчанию `0.0.0.0`. |
| `--netbird` | Второй адрес для клиентов из сети `100.64.0.0/10`. Нужен, только если часть игроков ходит по LAN, а часть по NetBird. |

## Структура проекта

```
amongus-server/
├── photon.py               серверы Name, Master и Game, комнаты, пересылка
├── dashboard.py            страница статуса и /api/status
├── dashboard.html          интерфейс дашборда
├── settings.py             настройки из окружения и .env
├── fusion_plugin.py        замена плагина Photon Fusion
├── enet.py                 надёжная доставка поверх UDP: подтверждения, повторы, фрагменты, пинг
├── crypto.py               обмен ключами Диффи-Хеллмана и AES для входа
├── protocol18.py           формат сообщений Photon
├── dns.py                  запасной DNS-сервер на один домен
├── test_local.py           тесты с локальным клиентом
├── requirements.txt        зависимости Python
├── Dockerfile              образ для Docker
├── compose.yaml            запуск через docker compose
├── amongus-photon.service  служба systemd
└── README.md
```

## Запасной DNS-сервер

Если в NetBird удобнее указать nameserver, а не запись, есть `dns.py`. Он отвечает только на `ns.photonengine.io` и отказывает на остальные имена:

```bash
python3 dns.py --bind 100.64.0.10 --port 5300 --answer 100.64.0.10
```

В NetBird тогда нужен nameserver `100.64.0.10:5300` с match domain `ns.photonengine.io`.

## Проверка

```bash
python3 test_local.py
```

Тесты занимают те же порты, что и сервер, поэтому запускайте их при остановленном сервере. Нужна библиотека `cryptography`.

## Если что-то не работает

| Что видно | Что проверить |
|---|---|
| Лобби не появляется на дашборде, игра ходит на официальные серверы | DNS: `nslookup ns.photonengine.io` на устройстве из сети NetBird должен вернуть адрес сервера. |
| Пинг до сервера есть, но игра висит на загрузке | Правило доступа NetBird должно разрешать протокол `ALL`, а не только ICMP. |
| Игрок подключается, но сразу отваливается | `PUBLIC_ADDRESS` должен совпадать с адресом в DNS-записи. |
| `/api/status` не отвечает | Сервер не запущен или дашборд на другом адресе. Посмотрите лог. |
| Windows: шлемы не подключаются, хотя DNS верный | Правило файрвола из шага 4. Там же проверьте, нет ли блокирующего правила для `python.exe`: оно появляется, если отказать в окне запроса Windows. |
| Windows: задание запущено, но сервера нет | Запустите `.venv\Scripts\python.exe photon.py` вручную: ошибка будет видна в консоли или в `LOG_FILE`. |
| В логе `dashboard ... not available yet` | Адрес из `DASHBOARD` ещё не поднят на сервере или порт занят другой программой. |

## Ограничения

- **Смена хоста не реализована.** Если хост выходит или вылетает, раунд обрывается у всех.
- **Нет авторизации.** Войти может любой, кто дошёл до сервера: закрывайте порты сетью.
- **Нет имён на дашборде.** Игра не передаёт серверу имена, поэтому игроки показаны по IP.
- **Внешние сервисы.** Игра по-прежнему ходит к серверам Oculus, Epic и Innersloth для входа и инвентаря. Если они недоступны, игра не запустится.
- **Один сервер на всех.** Если он выключен, не играет никто.
