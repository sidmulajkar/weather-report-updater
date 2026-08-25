"""Local verification harness: run the full pipeline with a fake Telegram
sender and print the produced text + delivered media list. No network send.
"""
import sys, os, glob, time
sys.path.insert(0, os.path.dirname(__file__))

import run_report as rr
from run_report import _verify_outputs
import delivery.telegram as tg

cap = {}
sent = []
sent_anim = []

def fake_msg(chat_id, text, dry_run=False, pace=1.0, parse_mode=None):
    cap['s'] = text
    return {"ok": True}

def fake_ph(chat_id, path, caption="", dry_run=False):
    sent.append((os.path.basename(path), caption))
    return {"ok": True}

def fake_anim(chat_id, path, caption="", dry_run=False, pace=1.0, supports_streaming=False):
    sent_anim.append((os.path.basename(path), caption, supports_streaming))
    return {"ok": True}

tg.send_message = fake_msg
tg.send_photo = fake_ph
tg.send_animation = fake_anim

t0 = time.time()
rr.main()
_verify_outputs(rr.BASE + '/output', True, True)
all_media = sent + [(f, c) for f, c, _ in sent_anim]
print(f"\nTOTAL_RUNTIME={time.time()-t0:.1f}s  TEXT_LEN={len(cap.get('s',''))}  SENT={len(all_media)}")
print("=" * 60)
print(cap.get('s', ''))

# --- local verification summary ---
out_dir = os.path.join(os.path.dirname(__file__), 'output')
report_path = os.path.join(out_dir, 'report.md')
media_exts = {'.png', '.gif', '.mp4', '.jpg'}
paths = []
if os.path.isdir(out_dir):
    for name in os.listdir(out_dir):
        if os.path.isfile(os.path.join(out_dir, name)):
            ext = os.path.splitext(name)[1].lower()
            if ext in media_exts:
                paths.append(name)
paths.sort()
print("\n" + "=" * 60)
print("LOCAL VERIFICATION SUMMARY")
print("=" * 60)
print(f"report.md : {report_path} {'exists' if os.path.exists(report_path) else 'MISSING'}")
print(f"media     : {len(paths)} file(s)")
for name in paths:
    full = os.path.join(out_dir, name)
    size = os.path.getsize(full)
    print(f"  {name}: {size/1024:.1f} KB")
print("\nNote: in production, Telegram would attach these media in severity-gated mode:")
print("  NORMAL   -> state_synoptic_brief.png + mmr_asset_brief.mp4")
print("  SEVERE   -> + nowcast_map.png, severe_map.png, district_choropleth.png, timing_map.png")
print("=" * 60)
