import subprocess, os, threading, queue, time, re, json
from datetime import datetime

class VoiceModule:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        
        # Жёсткая зачистка старых процессов
        subprocess.run(['pkill', '-9', 'ffplay'], capture_output=True)
        subprocess.run(['pkill', '-9', 'mpg123'], capture_output=True)

        self.voice_path = os.path.expanduser(config.get('path', '~/voice_files/'))
        self.default_volume = config.getint('default_volume', 80)
        self.card = config.get('control_card', '0')
        self.device = config.get('control_device', 'Master')
        self.work_start = config.get('work_start', '06:00')
        self.work_end = config.get('work_end', '21:25')
        
        stations_str = config.get('stations', '')
        self.stations = [s.strip() for s in stations_str.split(',') if s.strip()]
        
        self.queue = queue.Queue()
        self.fm_process = None
        self.voice_process = None
        self.current_track = ""
        self.current_station = ""
        self.last_url = None 
        self.lock = threading.Lock() 
        self.state_file = os.path.expanduser("~/voice_state.json")
        self.is_offline = False

        self._load_state_silent()

        self.printer.register_event_handler("klippy:disconnect", self._finalize)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        
        threading.Thread(target=self._voice_worker, daemon=True).start()
        threading.Thread(target=self._time_monitor, daemon=True).start()
        threading.Thread(target=self._metadata_spy, daemon=True).start()

        self.gcode.register_command('VOICE', self.cmd_VOICE)
        self.gcode.register_command('SET_VOLUME', self.cmd_SET_VOLUME)
        self.gcode.register_command('CLEAR_VOICE', self.cmd_CLEAR_VOICE)
        self.gcode.register_command('FM', self.cmd_FM)
        self.gcode.register_command('FM_STOP', self.cmd_FM_STOP)
        self.gcode.register_command('FM_LIST', self.cmd_FM_LIST)

    def _display(self, msg, type="echo"):
        """Вывод с защитой от 'призрачных' сообщений после остановки"""
        delay = 0.1
        if "Радио:" in msg: delay = 2
        if "Жанр:" in msg: delay = 3.5
        if "играет:" in msg: delay = 5

        def _send(eventtime):
            # Если это метаданные, но радио уже выключили (fm_stop), то молчим
            metatags = ["Радио:", "Жанр:", "играет:", "✅ FM:"]
            time.sleep(1)
            if any(tag in msg for tag in metatags) and not self.last_url:
                return 
            
            try:
                self.gcode.run_script(f'RESPOND TYPE={type} MSG="{msg}"')
            except:
                pass
        
        self.reactor.register_callback(_send, self.reactor.monotonic() + delay)

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'volume': self.default_volume, 'last_url': self.last_url}, f)
        except: pass

    def _load_state_silent(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.default_volume = state.get('volume', self.default_volume)
                    self.last_url = state.get('last_url', None)
            except: pass
        self._set_sys_volume(self.default_volume if self._is_work_time() else 0)

    def _is_work_time(self):
        now = datetime.now().strftime('%H:%M')
        if self.work_start <= self.work_end:
            return self.work_start <= now <= self.work_end
        return now >= self.work_start or now <= self.work_end

    def _metadata_spy(self):
        while True:
            # Проверяем не только наличие процесса, но и наличие last_url
            if self.fm_process and self.fm_process.poll() is None and self.last_url:
                try:
                    cmd = ['ffprobe', '-v', 'quiet', '-show_format', '-icy', '1', self.last_url]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=7)
                    
                    # Еще одна проверка: не нажали ли STOP пока работал ffprobe (он долгий)
                    if not self.last_url:
                        continue

                    match = re.search(r"TAG:StreamTitle=(.*)", res.stdout)
                    if match:
                        track = match.group(1).strip()
                        if track and track != self.current_track:
                            self.current_track = track
                            self._display(f"🎵 Сейчас играет: {self.current_track}")
                except: pass
            time.sleep(10)


    def _metadata_worker(self, pipe):
        connected = False
        for line in iter(pipe.readline, ''):
            try:
                line_str = line.strip()
                if not line_str or "M-A:" in line_str: continue
                if "Input #0" in line_str and not connected:
                    connected = True
                    self._display("✅ FM: Соединение установлено")
                if 'icy-name' in line_str:
                    m = re.search(r"icy-name\s*:\s*(.*)", line_str)
                    if m:
                        val = m.group(1).strip()
                        if val and val != self.current_station:
                            self.current_station = val
                            self._display(f"📻 Радио: {self.current_station}")
                if 'icy-genre' in line_str:
                    m = re.search(r"icy-genre\s*:\s*(.*)", line_str)
                    if m: self._display(f"🎶 Жанр: {m.group(1).strip()}")
                if 'StreamTitle' in line_str:
                    m = re.search(r"StreamTitle\s*:\s*(.*)", line_str)
                    if m:
                        track = m.group(1).strip().strip("'").strip('"')
                        if track and track != self.current_track:
                            self.current_track = track
                            self._display(f"🎵 Сейчас играет: {self.current_track}")
            except: continue
        pipe.close()

    def _has_internet(self):
        try:
            subprocess.run(['ping', '-c', '1', '-W', '2', '8.8.8.8'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except: return False

    def _handle_ready(self):
        if self.last_url and self._is_work_time() and not self.fm_process:
            time.sleep(2.0)
            self._set_sys_volume(self.default_volume)
            self._start_fm(self.last_url)
            self._display("🚀 FM: Система готова, автозапуск...")

    def _time_monitor(self):
        while True:
            try:
                if self._is_work_time():
                    has_net = self._has_internet()
                    if self.fm_process and not has_net:
                        if not self.is_offline:
                            self._display("⚠️ FM: Потеря соиденения...")
                            self.is_offline = True
                        try: os.killpg(os.getpgid(self.fm_process.pid), 9)
                        except: pass
                        self.fm_process = None
                    elif self.last_url and not self.fm_process and self.is_offline:
                        if has_net:
                            self._display("🌐 FM: Соединение восстановлено!")
                            self.is_offline = False
                            self._set_sys_volume(self.default_volume)
                            self._start_fm(self.last_url)
                else:
                    if self.fm_process:
                        try: os.killpg(os.getpgid(self.fm_process.pid), 9)
                        except: pass
                        self.fm_process = None
            except: pass
            time.sleep(5)

    def _start_fm(self, url):
        if not self._is_work_time(): return
        with self.lock:
            subprocess.run(['pkill', '-9', 'ffplay'], capture_output=True)
            try:
                self.fm_process = subprocess.Popen(
                    ['ffplay', '-nodisp', '-loglevel', 'info', '-icy', '1', 
                     '-probesize', '32', '-analyzeduration', '0', url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, 
                    bufsize=1, universal_newlines=True, errors='replace', preexec_fn=os.setsid
                )
                self.current_track, self.current_station = "", ""
                threading.Thread(target=self._metadata_worker, args=(self.fm_process.stderr,), daemon=True).start()
            except Exception as e:
                self._display(f"FM Error: {str(e)}")

    def _voice_worker(self):
        while True:
            filename = self.queue.get()
            if filename is None: break
            if not self._is_work_time(): self.queue.task_done(); continue
            try:
                with self.lock:
                    if self.fm_process and self.fm_process.poll() is None:
                        os.killpg(os.getpgid(self.fm_process.pid), 9); self.fm_process = None
                self._set_sys_volume(100)
                self.voice_process = subprocess.Popen(['mpg123', '-q', filename])
                self.voice_process.wait()
            except: pass
            finally:
                self.voice_process = None
                time.sleep(0.1)
                if self.queue.empty():
                    self._set_sys_volume(self.default_volume)
                    if self.last_url and self._is_work_time():
                        time.sleep(0.5); self._start_fm(self.last_url)
                self.queue.task_done()

    def cmd_FM(self, gcmd):
        p = gcmd.get_command_parameters()
        raw = gcmd.get_raw_command_parameters().strip()
        url, idx_s = p.get('URL'), p.get('S')
        if idx_s:
            try:
                idx = int(idx_s)
                url = self.stations[idx - 1] if 1 <= idx <= len(self.stations) else None
                if not url: self._display(f"⚠ Ошибка: Станции №{idx} нет."); return
            except: url = idx_s
        elif not url and raw:
            if raw.isdigit():
                idx = int(raw)
                url = self.stations[idx - 1] if 1 <= idx <= len(self.stations) else None
            else: url = raw
        if not url: self._display("FM: Укажите S= или URL="); return
        self.last_url = url
        self._save_state()
        if not self._is_work_time():
            self._display("🌙 FM: Сейчас время тишины. Станция запомнена.")
            return
        if self.voice_process or not self.queue.empty():
            self._display("📡 FM: Добавлена в план (после уведомлений).")
            return
        self.cmd_FM_STOP(None)
        self._start_fm(url)
        d_name = f"№{idx_s}" if idx_s and idx_s.isdigit() else "по ссылке"
        self._display(f"📡 FM: Подключение {d_name}...")

    def cmd_FM_STOP(self, gcmd):
        if gcmd is not None: 
            self.last_url, self.is_offline = None, False
            self._save_state()
        with self.lock:
            if self.fm_process:
                try: os.killpg(os.getpgid(self.fm_process.pid), 9)
                except: pass
                self.fm_process = None
            subprocess.run(['pkill', '-9', 'ffplay'], capture_output=True)
            self.current_track, self.current_station = "", ""
            if gcmd: self._display("FM: Воспроизведение остановлено")

    def cmd_VOICE(self, gcmd):
        if not self._is_work_time(): return
        p = gcmd.get_command_parameters()
        filename = p.get('S', gcmd.get_raw_command_parameters().strip())
        if not filename: return
        if not filename.lower().endswith('.mp3'): filename += '.mp3'
        full_path = os.path.join(self.voice_path, filename)
        if os.path.exists(full_path): self.queue.put(full_path)

    def cmd_SET_VOLUME(self, gcmd):
        try:
            vol = int(gcmd.get_command_parameters().get('S', gcmd.get_raw_command_parameters().strip()))
            vol = max(0, min(100, vol))
            self.default_volume = vol; self._set_sys_volume(vol); self._save_state()
            self._display(f"Громкость: {vol}%")
        except: pass

    def cmd_FM_LIST(self, gcmd):
        if self.stations:
            gcmd.respond_info("Радиостанции:\n" + "\n".join([f"{i+1}: {u}" for i, u in enumerate(self.stations)]))

    def cmd_CLEAR_VOICE(self, gcmd):
        with self.lock:
            with self.queue.mutex: self.queue.queue.clear()
            if self.voice_process:
                try: self.voice_process.terminate()
                except: pass
        if gcmd: self._display("Voice: Очередь очищена")

    def _set_sys_volume(self, val):
        try:
            val = max(0, min(100, val))
            subprocess.run(['amixer', '-c', self.card, 'set', self.device, f'{val}%'], capture_output=True)
        except: pass

    def _finalize(self):
        with self.lock:
            if self.fm_process:
                try: os.killpg(os.getpgid(self.fm_process.pid), 9)
                except: pass
            subprocess.run(['pkill', '-9', 'mpg123'], capture_output=True)

def load_config(config): return VoiceModule(config)
