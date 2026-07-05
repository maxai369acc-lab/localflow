"""Generate localflow.ico (multi-size) from the tray artwork."""
from localflow.ui import make_icon

img = make_icon("recording").resize((256, 256))
img.save("localflow.ico",
         sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
print("localflow.ico written")
