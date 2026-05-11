# Klipper Voice & Radio Module (KVR)

Интеллектуальный аудио-модуль для Klipper, превращающий ваш 3D-принтер в полноценный медиа-центр. Поддерживает голосовые уведомления с системой очередей, интернет-радио с динамическими метаданными и автоматический режим тишины.

---

## 🚀 Основные возможности

*   **Система очередей (Cascade)**: Уведомления проигрываются последовательно. Радио автоматически отключается на время речи и возвращается по завершении всей очереди.
*   **Динамические метаданные**: Вывод названия радиостанции, жанра и текущего трека на KlipperScreen и в консоль (через систему "Шпион" с помощью `ffprobe`).
*   **Автономность и Smart Reconnect**: Интеллектуальный мониторинг интернет-соединения. При обрыве связи радио отключается, а при восстановлении — автоматически возобновляет вещание.
*   **Memory State**: Модуль запоминает последнюю громкость, активный поток и имена радиостанций в файл `voice_state.json`. После перезагрузки всё восстановится автоматически.
*   **Quiet Hour (Тихий час)**: Автоматическое отключение звука в заданное время, очистка очередей уведомлений и автоматическое пробуждение системы утром.
*   **Smart Volume**: Голосовые уведомления всегда звучат на 100% мощности канала, радио — на заданном вами уровне.

---

## 🛠 Установка

### Шаг 1: Установка зависимостей

sudo apt update
sudo apt install -y mpg123 alsa-utils ffmpeg pulseaudio pulseaudio-module-bluetooth bluez

### Шаг 2: Настройка прав доступа

sudo usermod -aG audio,pulse-access,bluetooth klipper

⚠️ Обязательно перезагрузите систему после этого шага: sudo reboot

### Шаг 3: Подготовка файлов

mkdir -p ~/voice_files
cp voice.py ~/klipper/klippy/extras/

### Шаг 4: Выбор аудиоустройства и настройка

Модуль поддерживает три сценария подключения. Выберите ваш и следуйте инструкции:

---

## 🔌 СЦЕНАРИЙ А: Проводные колонки / наушники (ALSA)

Самый простой способ. Подходит если у вас обычные проводные колонки или наушники, воткнутые в аудиоразъём 3.5мм или USB-звуковую карту.

### Настройка:

1. Узнайте имя вашей звуковой карты:
   aplay -l
   Вы увидите список устройств. Запомните номер карты (обычно 0 для встроенной, 1 для USB).

2. Узнайте имя регулятора громкости:
   amixer scontrols
   Обычно это Master, Speaker или Headphone.

3. Добавьте в printer.cfg:
   [voice]
   output_type: alsa
   control_card: 0              # Номер карты из aplay -l
   control_device: Master       # Имя регулятора из amixer scontrols
   # ... остальные параметры

Готово! Переходите к разделу «Общая конфигурация».

---

## 🔊 СЦЕНАРИЙ Б: Проводные колонки через PulseAudio

Используйте этот способ, если у вас уже установлен PulseAudio (например, вместе с графическим окружением), или если вы хотите более гибкое управление звуком.

### Настройка:

1. Создайте Unix-сокет для Klipper:
   Откройте файл конфигурации PulseAudio:
   sudo nano /etc/pulse/default.pa
   Добавьте в самый конец файла строку:
   load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-socket

2. Узнайте имя вашего устройства (sink):
   pactl list sinks short
   Вы увидите список устройств. Пример имени:
   alsa_output.usb-C-Media_Electronics_USB_Audio_Device-00.analog-stereo

3. Добавьте в printer.cfg:
   [voice]
   output_type: pulse
   control_device: @DEFAULT_SINK@   # Либо укажите конкретное имя из пункта 2
   # ... остальные параметры

Готово! Переходите к разделу «Общая конфигурация».

---

## 🎧 СЦЕНАРИЙ В: Bluetooth-колонка (PulseAudio)

Самый сложный, но удобный сценарий. Требует настройки автоматического подключения колонки.

### Настройка Bluetooth:

1. Убедитесь, что Bluetooth работает:
   sudo systemctl status bluetooth

2. Настройте авто-подключение:
   Откройте файл:
   sudo nano /etc/bluetooth/main.conf
   Найдите и раскомментируйте (уберите #) строки:
   AutoEnable=true
   FastConnectable=true
   ReconnectAttempts=0

3. Настройте PulseAudio для Bluetooth:
   Откройте файл:
   sudo nano /etc/pulse/default.pa
   Добавьте в самый конец файла эти 3 строки:
   load-module module-bluetooth-policy
   load-module module-bluetooth-discover
   load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-socket

4. Перезагрузите систему:
   sudo reboot

### Сопряжение с колонкой (делается один раз):

bluetoothctl
power on
agent on
default-agent
scan on
# Включите вашу колонку в режим сопряжения
# Дождитесь появления её адреса, например: 12:11:06:D8:7A:17
pair 12:11:06:D8:7A:17
trust 12:11:06:D8:7A:17
connect 12:11:06:D8:7A:17
exit

### Установка колонки по умолчанию:

1. Узнайте имя вашей колонки:
   pactl list sinks short
   Найдите строку с bluez_sink. Пример:
   bluez_sink.12_11_06_D8_7A_17.a2dp_sink

2. Сделайте колонку устройством по умолчанию:
   pactl set-default-sink bluez_sink.12_11_06_D8_7A_17.a2dp_sink

### Добавьте в printer.cfg:

[voice]
output_type: pulse
control_device: bluez_sink.12_11_06_D8_7A_17.a2dp_sink
# ... остальные параметры

### Если колонка не подключается автоматически после перезагрузки:

Добавьте принудительное подключение в автозагрузку:

sudo nano /etc/rc.local

Добавьте перед exit 0 (заменив MAC-адрес на свой):
(sleep 15 && bluetoothctl connect 12:11:06:D8:7A:17) &

Сделайте файл исполняемым:
sudo chmod +x /etc/rc.local

Готово! Переходите к разделу «Общая конфигурация».

---

## ⚙️ Общая конфигурация

Добавьте секцию [voice] в ваш printer.cfg. Ниже минимальный рабочий пример:

[voice]
path: ~/voice_files/         # Путь к папке с mp3 файлами
default_volume: 80           # Громкость при первом запуске (0-100)
work_start: 08:00            # Время начала работы звука
work_end: 22:00              # Время ухода в режим тишины

stations:                    # Список URL радиостанций через запятую
    http://ep256.hostingradio.ru:8052/europaplus256.mp3,
    http://rusradio.hostingradio.ru/rusradio128.mp3,
    http://nashe1.hostingradio.ru/nashespb128.mp3

ℹ️ Важно: Формат времени work_start и work_end — ЧЧ:ММ (например 08:00 или 22:00). Если конец рабочего времени находится за полночью (например, с 22:00 до 02:00), укажите work_end: 02:00.

Для вывода сообщений на KlipperScreen добавьте:
[respond]

---

## 🕹 Команды G-Code

| Команда | Описание | Пример |
| :--- | :--- | :--- |
| VOICE S=имя | Воспроизвести имя.mp3 из папки звуков | VOICE S=finished |
| FM S=номер | Включить радио по номеру в списке | FM S=1 |
| FM URL=ссылка | Включить радио по прямой ссылке | FM URL=http://... |
| FM_STOP | Выключить радио (стирает URL из памяти) | FM_STOP |
| FM_LIST | Показать список станций с именами и URL | FM_LIST |
| SET_VOLUME S=X | Установить громкость от 0 до 100 | SET_VOLUME S=50 |
| CLEAR_VOICE | Очистить очередь, прервать текущий голос | CLEAR_VOICE |

---

## 💡 Полезные макросы (опционально)

Эти макросы можно добавить в printer.cfg для удобства:

#####################################################################
# ГОРЯЧИЕ КЛАВИШИ И УПРАВЛЕНИЕ ЗВУКОМ
#####################################################################

[gcode_macro FM]
variable_last_s: 1
rename_existing: FM_BASE
gcode:
  {% if params.S is undefined %}
    # Циклическое переключение станций
    {% set S = printer["gcode_macro FM"].last_s + 1 %}
    {% if S > 3 %} # Укажите своё количество станций
      {% set S = 1 %}
    {% endif %}
  {% else %}
    {% set S = params.S|int %}
  {% endif %}
  
  SET_GCODE_VARIABLE MACRO=FM VARIABLE=last_s VALUE={S}
  FM_BASE S={S}

[gcode_macro FM_NEXT]
gcode: FM

[gcode_macro FM_PREVIOUS]
gcode:
  {% set S = printer["gcode_macro FM"].last_s - 1 %}
  {% if S < 1 %}
    {% set S = 3 %} # Укажите своё количество станций
  {% endif %}
  FM S={S}

[gcode_macro VOLUME_UP]
gcode:
  {% set current_vol = printer["voice"].default_volume|default(80)|int %}
  {% set S = current_vol + 5 %}
  {% if S > 100 %}{% set S = 100 %}{% endif %}
  SET_VOLUME S={S}

[gcode_macro VOLUME_DOWN]
gcode:
  {% set current_vol = printer["voice"].default_volume|default(80)|int %}
  {% set S = current_vol - 5 %}
  {% if S < 0 %}{% set S = 0 %}{% endif %}
  SET_VOLUME S={S}

[gcode_macro VOICE_OFF]
gcode:
    CLEAR_VOICE
    FM_STOP
    M118 Sound system disabled (all muted)

---

## 🧪 Проверка работоспособности

1. Проверьте громкость:
   SET_VOLUME S=100

2. Проверьте голос (заранее положите файл test.mp3 в ~/voice_files/):
   VOICE S=test

3. Проверьте радио (если есть интернет):
   FM S=1

4. Проверьте список станций:
   FM_LIST

### Если звука нет:

1. Проверьте группу пользователя:
   groups klipper | grep audio
   Должно быть audio и pulse-access.

2. Проверьте железо (ALSA):
   speaker-test -c 2 -t sine
   Должен быть слышен писк. Если нет — проблема не в модуле, а в настройке звука в системе.

3. Проверьте PulseAudio (если используется):
   sudo -u klipper pactl list sinks short
   pactl stat

4. Проверьте сокет (для PulseAudio):
   ls -l /tmp/pulse-socket

---

## 📚 Документация

### Принцип работы голосовых уведомлений (VOICE)

*   Очередность: Уведомления играются строго друг за другом (каскад).
*   Приоритет громкости: На время речи громкость всегда 100%.
*   Очистка: CLEAR_VOICE мгновенно очищает очередь и убивает текущий процесс речи.

### Принцип работы радио (FM) и метаданных

*   Авто-пауза: При голосовом уведомлении радио выключается и автоматически возвращается после завершения очереди.
*   Smart Reconnect: Система мониторит интернет (пинг до 8.8.8.8). При обрыве радио отключается, при восстановлении — перезапускается.
*   Метаданные: При подключении извлекаются icy-name (станция) и icy-genre (жанр). Отдельный поток-шпион обновляет название трека.
*   Кэширование имён: При первом прослушивании радиостанции её имя из метаданных сохраняется в voice_state.json и используется в FM_LIST.

### Память состояний (Memory State)

*   В файле ~/voice_state.json сохраняются: громкость, последний URL, имена радиостанций.
*   После ребута громкость и станция восстанавливаются.
*   FM_STOP стирает URL — после ребута радио не включится.

### Режим тишины (Quiet Hour)

*   Настраивается через work_start и work_end.
*   В нерабочее время: радио и очередь очищаются, громкость = 0.
*   Утром (в work_start) система автоматически просыпается и запускает радио, если оно играло до тихого часа.

---

## 📦 Структура файлов

*   klippy/extras/voice.py — Ядро модуля.
*   ~/voice_files/ — Папка с вашими .mp3 файлами.
*   ~/voice_state.json — Файл памяти (создаётся автоматически).
