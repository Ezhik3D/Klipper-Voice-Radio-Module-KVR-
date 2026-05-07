import subprocess, os, threading, queue, time, re, json
from datetime import datetime

class VoiceModule:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        
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

        # 1. Загружаем память (без автозапуска)
        self._load_state_silent()

        self.printer.register_event_handler("klippy:disconnect", self._finalize)
        
        # 2. Запуск фоновых воркеров
        threading.Thread(target=self._voice_worker, daemon=True).start()
        threading.Thread(target=self._time_monitor, daemon=True).start()
        # Шпион для принудительного обновления метаданных
        threading.Thread(target=self._metadata_spy, daemon=True).start()

        self.gcode.register_command('VOICE', self.cmd_VOICE)
        self.gcode.register_command('SET_VOLUME', self.cmd_SET_VOLUME)
        self.gcode.register_command('CLEAR_VOICE', self.cmd_CLEAR_VOICE)
        self.gcode.register_command('FM', self.cmd_FM)
        self.gcode.register_command('FM_STOP', self.cmd_FM_STOP)
        self.gcode.register_command('FM_LIST', self.cmd_FM_LIST)

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
        
        if self._is_work_time():
            self._set_sys_volume(self.default_volume)
        else:
            self._set_sys_volume(0)

    def _is_work_time(self):
        now = datetime.now().strftime('%H:%M')
        if self.work_start <= self.work_end:
            return self.work_start <= now <= self.work_end
        return now >= self.work_start or now <= self.work_end

    def _metadata_spy(self):
        """Раз в 30 сек опрашивает сервер через ffprobe для обновления трека"""
        while True:
            if self.fm_process and self.fm_process.poll() is None and self.last_url:
                try:
                    cmd = ['ffprobe', '-v', 'quiet', '-show_format', '-icy', '1', self.last_url]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=7)
                    match = re.search(r"TAG:StreamTitle=(.*)", res.stdout)
                    if match:
                        track = match.group(1).strip()
                        if track and track != self.current_track:
                            self.current_track = track
                            self.gcode.respond_info(f"🎵 Сейчас играет: {self.current_track}")
                except: pass
            time.sleep(30)

    def _metadata_worker(self, pipe):
        connected = False
        for line in iter(pipe.readline, ''):
            try:
                line_str = line.strip()
                if not line_str or "M-A:" in line_str: continue

                if "Input #0" in line_str and not connected:
                    connected = True
                    self.gcode.respond_info("✅ FM: Соединение установлено")

                if 'icy-name' in line_str:
                    m = re.search(r"icy-name\s*:\s*(.*)", line_str)
                    if m:
                        val = m.group(1).strip()
                        if val and val != self.current_station:
                            self.current_station = val
                            self.gcode.respond_info(f"📻 Радио: {self.current_station}")

                if 'icy-genre' in line_str:
                    m = re.search(r"icy-genre\s*:\s*(.*)", line_str)
                    if m: self.gcode.respond_info(f"🎶 Жанр: {m.group(1).strip()}")

                if 'StreamTitle' in line_str:
                    m = re.search(r"StreamTitle\s*:\s*(.*)", line_str)
                    if m:
                        track = m.group(1).strip().strip("'").strip('"')
                        if track and track != self.current_track:
                            self.current_track = track
                            self.gcode.respond_info(f"🎵 Сейчас играет: {self.current_track}")
            except: continue
        pipe.close()

    def _time_monitor(self):
        while True:
            try:
                is_now_work = self._is_work_time()
                if not is_now_work:
                    if self.fm_process or not self.queue.empty() or self.voice_process:
                        with self.lock:
                            with self.queue.mutex: self.queue.queue.clear()
                            if self.fm_process: os.killpg(os.getpgid(self.fm_process.pid), 9); self.fm_process = None
                            if self.voice_process: self.voice_process.terminate(); self.voice_process = None
                        self._set_sys_volume(0)
                        self.gcode.respond_info("🌙 Voice: Режим тишины.")
                else:
                    if self.last_url and not self.fm_process and not self.voice_process and self.queue.empty():
                        time.sleep(2.0)
                        self._set_sys_volume(self.default_volume)
                        self._start_fm(self.last_url)
            except: pass
            time.sleep(5)

    def _start_fm(self, url):
        if not self._is_work_time(): return
        with self.lock:
            if self.fm_process: return
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
                self.gcode.respond_info(f"FM Error: {str(e)}")

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
                        time.sleep(1.2); self._start_fm(self.last_url)
                self.queue.task_done()

    def cmd_FM(self, gcmd):
        p = gcmd.get_command_parameters()
        raw = gcmd.get_raw_command_parameters().strip()
        idx_s = p.get('S', raw)
        url = None
        
        # Определяем URL
        if idx_s:
            if idx_s.isdigit():
                idx = int(idx_s)
                url = self.stations[idx - 1] if 1 <= idx <= len(self.stations) else None
            elif "http" in idx_s: 
                url = idx_s
        
        if not url:
            gcmd.respond_info("FM Error: Станция не найдена")
            return

        # ЗАПОМИНАЕМ URL как главную цель
        self.last_url = url
        self._save_state()

        # Если сейчас тихий час
        if not self._is_work_time():
            gcmd.respond_info(f"🌙 FM: Сейчас время тишины. Станция №{idx_s} запомнена и включится в рабочее время.")
            return

        # Если сейчас идет озвучка или очередь не пуста
        if self.voice_process or not self.queue.empty():
            # ВОТ ЭТА СТРОЧКА, КОТОРУЮ МЫ ЗАБЫЛИ:
            gcmd.respond_info(f"📡 FM: Станция №{idx_s} добавлена в план. Она включится автоматически сразу после уведомлений VOICE.")
            return

        # Если принтер молчит — запускаем сразу
        self.cmd_FM_STOP(None) # Тихий стоп без затирки last_url
        self._start_fm(url)
        gcmd.respond_info(f"📡 FM: Подключение к станции №{idx_s}...")


    def cmd_FM_STOP(self, gcmd):
        if gcmd is not None: self.last_url = None; self._save_state()
        with self.lock:
            if self.fm_process:
                try: os.killpg(os.getpgid(self.fm_process.pid), 9)
                except: pass
                self.fm_process, self.current_track, self.current_station = None, "", ""
                if gcmd: gcmd.respond_info("FM: Остановлено")

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
            gcmd.respond_info(f"Громкость: {vol}%")
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
        if gcmd: gcmd.respond_info("Voice: Очередь очищена")

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
