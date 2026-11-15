from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    highpass: float  # Hz
    lowpass: float  # Hz
    limit: float  # 0..1 (linear); 0.7 ~ -3.1 dBFS


# Valores iniciais: validar de ouvido na bobina e ajustar.
PRESETS = {
    "default": Preset(highpass=150, lowpass=8000, limit=0.7),
    "vocal": Preset(highpass=200, lowpass=4000, limit=0.7),
    "safe": Preset(highpass=150, lowpass=6000, limit=0.5),
}
