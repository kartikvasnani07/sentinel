import math
import shutil
import sys
import threading
import time


class TerminalWaveRenderer:
    MODES = {"idle", "wake", "recording", "processing", "responding", "speaking", "paused"}
    STYLES = {"waves", "bubble"}
    BUBBLE_VIRTUAL_GRID = 300

    def __init__(self, enabled=True, fps=18, min_height=3, style="waves"):
        self.enabled = bool(enabled)
        self.fps = max(10, int(fps))
        self.min_height = max(3, int(min_height))

        self._lock = threading.Lock()
        self._io_lock = threading.RLock()
        self._running = False
        self._thread = None
        self._mode = "idle"
        self._style = "waves" if str(style or "").strip().lower() not in self.STYLES else str(style or "").strip().lower()
        self._audio_level = 0.0
        self._pulse = 0.0
        self._drawn_lines = 0
        self._drawn_width = 0

    def start(self):
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="terminal-wave")
        self._thread.start()

    def close(self):
        if not self.enabled:
            return
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        self._erase_block()

    def set_mode(self, mode):
        if mode not in self.MODES:
            return
        with self._lock:
            self._mode = mode

    def set_style(self, style):
        candidate = str(style or "").strip().lower()
        if candidate not in self.STYLES:
            return
        with self._lock:
            self._style = candidate

    def set_enabled(self, enabled):
        enable_value = bool(enabled)
        if enable_value == self.enabled and (not enable_value or self._running):
            return
        if enable_value:
            self.enabled = True
            self.start()
            self.set_mode("idle")
            return
        self.pause_and_clear()
        self.close()
        self.enabled = False

    def pulse(self, amount=1.0):
        with self._lock:
            self._pulse = max(self._pulse, min(2.4, float(amount)))

    def set_audio_level(self, value):
        with self._lock:
            level = float(value or 0.0)
            # Typical RMS is small; expand to a useful [0..1] range.
            scaled = max(0.0, min(1.0, level * 32.0))
            self._audio_level = (self._audio_level * 0.7) + (scaled * 0.3)

    def clear_for_response(self):
        if not self.enabled:
            return
        with self._io_lock:
            self._erase_block()
            sys.stdout.write("\r")
            sys.stdout.flush()

    def pause_and_clear(self):
        if not self.enabled:
            return
        with self._lock:
            self._mode = "paused"
        self.clear_for_response()

    def clear_line(self):
        if not self.enabled:
            return
        with self._io_lock:
            width = max(self._drawn_width, shutil.get_terminal_size((120, 30)).columns)
            sys.stdout.write("\r" + (" " * width) + "\r")
            sys.stdout.flush()

    def _run(self):
        t0 = time.time()
        while self._running:
            with self._lock:
                mode = self._mode
                style = self._style
                audio_level = self._audio_level
                pulse = self._pulse
                self._pulse = max(0.0, self._pulse * 0.90)
                self._audio_level = max(0.0, self._audio_level * 0.95)

            if mode == "paused":
                self._erase_block()
                time.sleep(0.08)
                continue

            frame_time = time.time() - t0
            cols, rows = shutil.get_terminal_size((120, 30))
            width = max(1, cols)
            if style == "bubble":
                # Bubble mode uses the full terminal canvas so the sphere stays centered.
                height = max(10, rows - 2)
            else:
                height = max(3, min(self.min_height, rows - 6))
            try:
                if style == "bubble":
                    lines = self._build_bubble_frame(width=width, height=height, t=frame_time, mode=mode, audio=audio_level, pulse=pulse)
                else:
                    lines = self._build_ocean_frame(width=width, height=height, t=frame_time, mode=mode, audio=audio_level, pulse=pulse)
            except Exception:
                # Keep renderer alive even if a frame calculation fails.
                lines = self._build_fallback_frame(width=width, height=height, t=frame_time)
            self._draw_block(lines)
            time.sleep(1.0 / self.fps)

    def _build_fallback_frame(self, width, height, t):
        safe_width = max(1, int(width))
        safe_height = max(3, int(height))
        lines = [[" " for _ in range(safe_width)] for _ in range(safe_height)]
        crest = max(1, min(safe_height - 2, int((safe_height * 0.58) + (math.sin(t * 1.8) * 1.5))))
        for x in range(safe_width):
            lines[crest][x] = "~"
            for y in range(crest + 1, safe_height):
                lines[y][x] = "." if (x + y) % 2 == 0 else ":"
        return ["".join(row) for row in lines]

    def _draw_block(self, lines):
        if not lines:
            return
        with self._io_lock:
            current_lines = len(lines)
            current_width = max(len(line) for line in lines)
            previous_lines = self._drawn_lines
            previous_width = self._drawn_width

            if previous_lines > 0:
                sys.stdout.write(f"\x1b[{previous_lines}F")

            pad_width = max(previous_width, current_width)
            total_rows = max(previous_lines, current_lines)

            for idx in range(total_rows):
                row = lines[idx] if idx < current_lines else ""
                sys.stdout.write("\r" + row.ljust(pad_width))
                if idx < total_rows - 1:
                    sys.stdout.write("\n")

            # Keep cursor anchored at the bottom of the currently rendered block.
            if total_rows > current_lines:
                sys.stdout.write(f"\x1b[{total_rows - current_lines}F")
            sys.stdout.flush()
            self._drawn_lines = current_lines
            self._drawn_width = current_width

    def _erase_block(self):
        with self._io_lock:
            if self._drawn_lines <= 0:
                return
            sys.stdout.write(f"\x1b[{self._drawn_lines}F")
            blank = " " * max(1, self._drawn_width)
            for idx in range(self._drawn_lines):
                sys.stdout.write("\r" + blank)
                if idx < self._drawn_lines - 1:
                    sys.stdout.write("\n")
            sys.stdout.write("\r")
            sys.stdout.flush()
            self._drawn_lines = 0
            self._drawn_width = 0

    def _mode_profile(self, mode):
        profiles = {
            "idle": {"amp": 0.9, "speed": 0.7, "foam": 0.18, "splash": 0.06},
            "wake": {"amp": 2.2, "speed": 1.8, "foam": 0.34, "splash": 0.28},
            "recording": {"amp": 1.4, "speed": 1.3, "foam": 0.24, "splash": 0.16},
            "processing": {"amp": 1.0, "speed": 1.0, "foam": 0.20, "splash": 0.08},
            "responding": {"amp": 1.6, "speed": 1.4, "foam": 0.28, "splash": 0.20},
            "speaking": {"amp": 1.8, "speed": 1.6, "foam": 0.30, "splash": 0.24},
        }
        return profiles.get(mode, profiles["idle"])

    def _build_ocean_frame(self, width, height, t, mode, audio, pulse):
        width = max(1, int(width))
        height = max(3, int(height))
        if height <= 4:
            return self._build_compact_wave_frame(width, height, t, mode, audio, pulse)
        profile = self._mode_profile(mode)
        amp = profile["amp"] + (audio * 3.2) + (pulse * 1.8)
        speed = profile["speed"] + (audio * 0.8)
        foam_bias = profile["foam"] + (audio * 0.35) + (pulse * 0.25)
        splash_strength = profile["splash"] + (audio * 0.4) + (pulse * 0.5)

        # Keep a top air band and draw the ocean body in lower rows.
        base = height * 0.55
        density_chars = " .,:;irsXA253hMHGS#9B&@"
        spray_chars = ".`'~^"
        lines = [[" " for _ in range(width)] for _ in range(height)]

        centers = (
            width * (0.18 + 0.10 * math.sin(t * 0.8)),
            width * (0.52 + 0.18 * math.sin(t * 0.55 + 1.3)),
            width * (0.82 + 0.06 * math.sin(t * 1.2 + 0.7)),
        )

        surfaces = [0.0] * width
        for x in range(width):
            xf = x / max(1, width - 1)
            y = base
            y += math.sin((xf * 3.3 + t * speed * 0.42) * math.pi * 2.0) * amp * 0.55
            y += math.sin((xf * 6.7 - t * speed * 0.77) * math.pi * 2.0) * amp * 0.36
            y += math.sin((xf * 12.0 + t * speed * 1.05) * math.pi * 2.0) * amp * 0.18

            # Circular ripple packets to emulate splashes/impacts.
            ripple = 0.0
            for center in centers:
                d = abs(x - center) / max(1.0, width * 0.11)
                packet = math.sin((d * 3.8 - t * speed * 2.9) * math.pi * 2.0) * math.exp(-d * 1.4)
                ripple += packet
            y += ripple * (0.7 + splash_strength * 2.0)

            surfaces[x] = max(1.0, min(height - 2.0, y))

        for x in range(width):
            surface = surfaces[x]
            top = max(0, min(height - 1, int(surface)))
            frac = surface - top

            if 0 <= top < height:
                crest_pick = int(min(len(spray_chars) - 1, max(0, round((frac + foam_bias) * (len(spray_chars) - 1)))))
                lines[top][x] = spray_chars[crest_pick]

            # Fill water body below the surface.
            for y in range(top + 1, height):
                depth = (y - surface) / max(1.0, height - surface)
                turb = 0.5 + 0.5 * math.sin((x * 0.13 + y * 0.39 + t * speed * 1.7))
                val = min(1.0, max(0.0, depth * 0.82 + turb * 0.18))
                idx = int(val * (len(density_chars) - 1))
                lines[y][x] = density_chars[idx]

            # Add deterministic splash droplets above crests.
            if splash_strength > 0.04 and top >= 2:
                spray_gate = 0.5 + 0.5 * math.sin((x * 0.22 + t * speed * 4.1))
                if spray_gate > 0.88 - min(0.3, splash_strength):
                    spray_height = max(1, int(1 + (spray_gate - 0.85) * 14))
                    y0 = max(0, min(height - 1, top - spray_height))
                    if 0 <= y0 < height and 0 <= x < width:
                        lines[y0][x] = spray_chars[min(len(spray_chars) - 1, 2 + (x % 3))]

        # Optional light reflection stripe for a more ocean-like look.
        reflection_row = max(0, min(height - 1, int(height * 0.72 + math.sin(t * 0.8) * 1.5)))
        for x in range(0, width, 2):
            if lines[reflection_row][x] == " ":
                lines[reflection_row][x] = "."

        return ["".join(row) for row in lines]

    def _build_compact_wave_frame(self, width, height, t, mode, audio, pulse):
        profile = self._mode_profile(mode)
        amp = max(0.25, (profile["amp"] * 0.22) + (audio * 0.75) + (pulse * 0.45))
        speed = profile["speed"] + (audio * 0.5)
        lines = [[" " for _ in range(width)] for _ in range(height)]
        crest_chars = "~^~"
        base_row = max(0, min(height - 1, height - 2))

        for x in range(width):
            xf = x / max(1, width - 1)
            vertical = math.sin((xf * 3.0 + t * speed * 0.48) * math.pi * 2.0) * amp
            vertical += math.sin((xf * 6.6 - t * speed * 0.92) * math.pi * 2.0) * amp * 0.35
            row = max(0, min(height - 1, int(round(base_row + vertical))))
            lines[row][x] = crest_chars[x % len(crest_chars)]

            # subtle foam under crest without filling all rows into a rectangle
            if row + 1 < height and ((x + int(t * 10)) % 9 == 0):
                lines[row + 1][x] = "."

            # sparse splash for wake/recording/speaking peaks
            if row > 0 and (profile["splash"] + pulse * 0.2 + audio * 0.2) > 0.25 and ((x + int(t * 18)) % 23 == 0):
                lines[row - 1][x] = "'"

        return ["".join(row) for row in lines]

    def _build_bubble_frame(self, width, height, t, mode, audio, pulse):
        width = max(1, int(width))
        height = max(3, int(height))
        if height <= 6:
            return self._build_compact_bubble_frame(width, height, t, mode, audio, pulse)

        profile = self._bubble_mode_profile(mode)
        intensity = min(1.5, max(0.0, audio * 1.35 + pulse * 1.10))
        chars = " .,:-~=+*#%@"
        lines = [[" " for _ in range(width)] for _ in range(height)]

        # Project a 300x300 virtual sphere into terminal space.
        grid_size = float(self.BUBBLE_VIRTUAL_GRID)
        half_grid = grid_size * 0.5

        cx = (width - 1) * 0.5 + math.sin(t * profile["drift_x"]) * max(0.4, width * 0.012)
        cy = (height - 1) * 0.5 + math.cos(t * profile["drift_y"]) * max(0.2, height * 0.025)

        # Terminal character cells are taller than they are wide; compensate so the sphere looks round.
        terminal_aspect = max(1.35, min(2.30, width / max(1.0, height)))
        radius_x = max(8.0, min(width * 0.34, (height * terminal_aspect) * 0.40))
        radius_y = max(4.0, radius_x / terminal_aspect)

        breath = 1.0 + math.sin(t * profile["breath_speed"] + 0.45) * (0.016 + profile["breath_amount"] * 0.030)
        radius_x *= breath
        radius_y *= breath

        # 3D motion parameters change by mode.
        spin = t * (0.65 + profile["spin"] + intensity * 0.34)
        deform_strength = min(0.80, profile["deform"] + intensity * 0.42)
        aura_strength = min(1.00, profile["aura"] + intensity * 0.35)
        spark_strength = min(1.00, profile["spark"] + intensity * 0.40)

        # pseudo-3D lighting vector
        lx = -0.62 + math.sin(t * 0.48) * 0.08
        ly = -0.33 + math.cos(t * 0.41) * 0.06
        lz = 0.72
        light_norm = math.sqrt(lx * lx + ly * ly + lz * lz)
        lx, ly, lz = lx / light_norm, ly / light_norm, lz / light_norm

        x_norm = [((x + 0.5) - cx) / max(1e-6, radius_x) for x in range(width)]
        y_norm = [((y + 0.5) - cy) / max(1e-6, radius_y) for y in range(height)]

        for y in range(height):
            ny = y_norm[y]
            wy = ny * half_grid
            for x in range(width):
                nx = x_norm[x]
                wx = nx * half_grid
                dist2 = nx * nx + ny * ny
                if dist2 <= 1.0:
                    z = math.sqrt(max(0.0, 1.0 - dist2))

                    # Layered displacement gives a more organic liquid-sphere motion.
                    wave_a = math.sin((wx * 0.064 + spin * 1.10) + (wy * 0.031))
                    wave_b = math.sin((wy * 0.058 - spin * 0.95) - (wx * 0.025))
                    wave_c = math.sin(((wx + wy) * 0.039) + spin * 1.35)
                    displacement = (wave_a * 0.38 + wave_b * 0.34 + wave_c * 0.28) * deform_strength * 0.14
                    displacement *= (1.0 - dist2) ** 0.55
                    depth = max(0.0, z + displacement)

                    # Approximate deformed normal.
                    nnx = nx + displacement * 0.85
                    nny = ny - displacement * 0.62
                    nnz = max(1e-6, depth)
                    normal_norm = math.sqrt(nnx * nnx + nny * nny + nnz * nnz)
                    nnx, nny, nnz = nnx / normal_norm, nny / normal_norm, nnz / normal_norm

                    diffuse = max(0.0, nnx * lx + nny * ly + nnz * lz)
                    fresnel = (1.0 - max(0.0, nnz)) ** 2.0
                    spec = diffuse ** (14 + int(profile["spec_power"] * 10))

                    shade = 0.18 + (depth * 0.45) + (diffuse * 0.52) + (spec * 0.34) + (fresnel * 0.18)
                    shade = min(1.0, max(0.0, shade))
                    idx = int(shade * (len(chars) - 1))
                    lines[y][x] = chars[idx]

                    # Dynamic rim spark during active states.
                    if dist2 > 0.84 and spark_strength > 0.35:
                        sparkle_gate = 0.5 + 0.5 * math.sin((x * 0.31) + (y * 0.27) + t * (4.8 + profile["spark"] * 2.2))
                        if sparkle_gate > (0.90 - spark_strength * 0.34):
                            lines[y][x] = "*" if mode in {"wake", "recording", "speaking"} else "+"
                    continue

                # Outer aura ring.
                if dist2 <= 1.18:
                    rim = 1.18 - dist2
                    aura_gate = 0.5 + 0.5 * math.sin((wx * 0.035) - (wy * 0.028) + t * (1.7 + profile["aura"]))
                    aura = rim * 1.9 * aura_strength + aura_gate * 0.16
                    if aura > 0.30:
                        lines[y][x] = "." if aura < 0.52 else ":"
                    if mode == "wake" and aura > 0.62:
                        lines[y][x] = "'"

        return ["".join(row) for row in lines]

    def _build_compact_bubble_frame(self, width, height, t, mode, audio, pulse):
        lines = [[" " for _ in range(width)] for _ in range(height)]
        center = (width - 1) * 0.5 + math.sin(t * 0.9) * max(0.5, width * 0.01)
        radius = max(2.0, min(width * 0.22, width / 2.0 - 1.0))
        chars = ".oO@"
        for x in range(width):
            dx = abs(x - center)
            if dx > radius:
                continue
            ring = dx / max(1e-6, radius)
            row = max(0, min(height - 1, int(round((height - 2) - math.cos((1.0 - ring) * math.pi) * 0.5))))
            idx = min(len(chars) - 1, int((1.0 - ring + audio * 0.25 + pulse * 0.18) * (len(chars) - 1)))
            lines[row][x] = chars[idx]
            if row > 0 and mode in {"wake", "recording", "speaking"} and ((x + int(t * 13)) % 17 == 0):
                lines[row - 1][x] = "'"
        return ["".join(row) for row in lines]

    def _bubble_mode_profile(self, mode):
        profiles = {
            "idle": {
                "deform": 0.18,
                "spin": 0.18,
                "aura": 0.24,
                "spark": 0.10,
                "breath_amount": 0.35,
                "breath_speed": 0.90,
                "drift_x": 0.70,
                "drift_y": 0.54,
                "spec_power": 1.35,
            },
            "wake": {
                "deform": 0.46,
                "spin": 0.46,
                "aura": 0.66,
                "spark": 0.72,
                "breath_amount": 0.65,
                "breath_speed": 1.65,
                "drift_x": 1.55,
                "drift_y": 1.20,
                "spec_power": 1.85,
            },
            "recording": {
                "deform": 0.36,
                "spin": 0.32,
                "aura": 0.46,
                "spark": 0.44,
                "breath_amount": 0.52,
                "breath_speed": 1.35,
                "drift_x": 1.12,
                "drift_y": 0.92,
                "spec_power": 1.62,
            },
            "processing": {
                "deform": 0.24,
                "spin": 0.62,
                "aura": 0.40,
                "spark": 0.16,
                "breath_amount": 0.42,
                "breath_speed": 1.05,
                "drift_x": 0.86,
                "drift_y": 0.74,
                "spec_power": 1.75,
            },
            "responding": {
                "deform": 0.33,
                "spin": 0.40,
                "aura": 0.48,
                "spark": 0.30,
                "breath_amount": 0.56,
                "breath_speed": 1.40,
                "drift_x": 1.02,
                "drift_y": 0.88,
                "spec_power": 1.70,
            },
            "speaking": {
                "deform": 0.40,
                "spin": 0.44,
                "aura": 0.55,
                "spark": 0.52,
                "breath_amount": 0.64,
                "breath_speed": 1.70,
                "drift_x": 1.22,
                "drift_y": 1.04,
                "spec_power": 1.82,
            },
        }
        return profiles.get(mode, profiles["idle"])
