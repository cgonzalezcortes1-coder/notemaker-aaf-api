import os, re, struct, json, tempfile
from fractions import Fraction
from io import BytesIO
from datetime import date
from http.server import BaseHTTPRequestHandler

try:
    import aaf2
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyaaf2'])
    import aaf2

SAMPLE_RATE = 48000
EDIT_RATE   = Fraction(SAMPLE_RATE, 1)
TC_RE       = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})(?:[:;]\d{1,2})?')

FPS_TABLE = {
    '23.976': Fraction(1001, 1000), '24': Fraction(1, 1),
    '25': Fraction(1, 1), '29.97': Fraction(1001, 1000),
    '30': Fraction(1, 1), '48': Fraction(1, 1),
    '50': Fraction(1, 1), '59.94': Fraction(1001, 1000), '60': Fraction(1, 1),
}

def parse_start(start_str):
    parts = re.split(r'[:;]', start_str.strip())
    if len(parts) < 3:
        raise ValueError(f"Start TC inválido: {start_str}")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

def parse_notes(text, default_dur, fps_str, start_str):
    if fps_str not in FPS_TABLE:
        raise ValueError(f"FPS no reconocido: {fps_str}")
    multiplier = FPS_TABLE[fps_str]
    offset     = float(parse_start(start_str) * multiplier)
    regions    = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        matches = list(TC_RE.finditer(line))
        if len(matches) >= 2:
            start = float(matches[0].group(0).split(':')[0]) * 3600 + \
                    float(matches[0].group(0).split(':')[1]) * 60 + \
                    float(matches[0].group(0).split(':')[2])
            start = start * float(multiplier) - offset
            end   = float(matches[1].group(0).split(':')[0]) * 3600 + \
                    float(matches[1].group(0).split(':')[1]) * 60 + \
                    float(matches[1].group(0).split(':')[2])
            end   = end * float(multiplier) - offset
            dur   = max(end - start, 1.0)
            name  = line[matches[1].end():].strip().lstrip('- ').strip()
        elif len(matches) == 1:
            g = matches[0].groups()
            start = (int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])) * float(multiplier) - offset
            dur   = float(default_dur)
            name  = line[matches[0].end():].strip().lstrip('- ').strip()
        else:
            continue
        if not name:
            name = f"Region_{int(start):06d}"
        if start < 0:
            continue
        regions.append((name, start, dur))
    return sorted(regions, key=lambda r: r[1])

def minimal_wav():
    buf = BytesIO()
    buf.write(b'RIFF'); buf.write(struct.pack('<I', 36))
    buf.write(b'WAVE')
    buf.write(b'fmt '); buf.write(struct.pack('<I', 16))
    buf.write(struct.pack('<HHIIHH', 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16))
    buf.write(b'data'); buf.write(struct.pack('<I', 0))
    return buf.getvalue()

def sr(sec):
    return int(round(sec * SAMPLE_RATE))

def build_aaf_bytes(regions, seq_name):
    with tempfile.NamedTemporaryFile(suffix='.aaf', delete=False, dir='/tmp') as tmp:
        tmp_path = tmp.name
    try:
        total = sr(regions[-1][1] + regions[-1][2] + 2.0)
        with aaf2.open(tmp_path, 'w') as f:
            comp = f.create.CompositionMob()
            comp.name = seq_name
            f.content.mobs.append(comp)
            tslot = comp.create_timeline_slot(edit_rate=EDIT_RATE)
            tslot.name = "A1"; tslot.slot_id = 1
            seq = f.create.Sequence(media_kind='sound')
            components = []; cursor = 0
            for name, start_sec, dur_sec in regions:
                ss, ds = sr(start_sec), sr(dur_sec)
                if ss > cursor:
                    components.append(f.create.Filler(media_kind='sound', length=ss - cursor))
                src_mob = f.create.SourceMob()
                src_mob.name = name
                desc = f.create.PCMDescriptor()
                desc['SampleRate'].value        = EDIT_RATE
                desc['AudioSamplingRate'].value = EDIT_RATE
                desc['Channels'].value          = 1
                desc['QuantizationBits'].value  = 16
                desc['BlockAlign'].value        = 2
                desc['AverageBPS'].value        = SAMPLE_RATE * 2
                desc['Length'].value            = ds
                src_mob['EssenceDescription'].value = desc
                f.content.mobs.append(src_mob)
                wav = minimal_wav()
                result = src_mob.create_essence(1, 'sound', 'wave', EDIT_RATE)
                ess = result[0] if isinstance(result, tuple) else result
                if hasattr(ess, 'write'): ess.write(wav)
                if hasattr(ess, 'close'): ess.close()
                master = f.create.MasterMob()
                master.name = name
                mslot = master.create_timeline_slot(edit_rate=EDIT_RATE)
                mslot.slot_id = 1
                mc = f.create.SourceClip(media_kind='sound', length=ds)
                mc['SourceID'].value        = src_mob.mob_id
                mc['SourceMobSlotID'].value = 1
                mc['StartTime'].value       = 0
                mslot.segment = mc
                f.content.mobs.append(master)
                clip = f.create.SourceClip(media_kind='sound', length=ds)
                clip['SourceID'].value        = master.mob_id
                clip['SourceMobSlotID'].value = 1
                clip['StartTime'].value       = 0
                components.append(clip); cursor = ss + ds
            if cursor < total:
                components.append(f.create.Filler(media_kind='sound', length=total - cursor))
            seq.components.extend(components)
            tslot.segment = seq
        with open(tmp_path, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


API_KEY = os.environ.get('NOTEMAKER_API_KEY', '')

class handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_cors(self):
        self.send_header('Access-Control-Allow-Origin', 'https://videoplayer-d7p.pages.dev')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key')
        self.send_header('Access-Control-Expose-Headers', 'Content-Disposition')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def do_POST(self):
        if self.headers.get('X-API-Key', '') != API_KEY or not API_KEY:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.send_cors()
            self.end_headers()
            self.wfile.write(b'{"error":"Unauthorized"}')
            return

        length = int(self.headers.get('Content-Length', 0))
        body   = json.loads(self.rfile.read(length))

        fps_str     = body.get('fps', '23.976')
        start_str   = body.get('start', '00:00:00:00')
        notes_txt   = body.get('notes', '')
        filename    = body.get('filename', 'notas').strip() or 'notas'
        default_dur = float(body.get('dur', 5))

        today    = date.today().strftime('%Y-%m-%d')
        aaf_name = f"{filename}_{today}.aaf"
        seq_name = f"{filename} — {today}"

        try:
            regions   = parse_notes(notes_txt, default_dur, fps_str, start_str)
            if not regions:
                raise ValueError("No se encontraron notas con timecode válido.")
            aaf_bytes = build_aaf_bytes(regions, seq_name)
        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', f'attachment; filename="{aaf_name}"')
        self.send_header('Content-Length', str(len(aaf_bytes)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(aaf_bytes)