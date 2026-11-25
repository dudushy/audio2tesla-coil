import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PATH = Path("device.json")
# notas testadas na calibragem e mostradas no soundpad: C1 (33 Hz) a B8 (7902 Hz)
COIL_LOW, COIL_HIGH = 24, 119


@dataclass(frozen=True)
class Profile:
    min_hz: float
    max_hz: float
    name: str = "plasma-speaker"
    notes: tuple[int, ...] = ()  # notas MIDI ouvidas na calibragem; vazio = a faixa inteira vale

    def __post_init__(self):
        if not 0 < self.min_hz < self.max_hz:
            raise ValueError(
                f"faixa inválida: min={self.min_hz:g} max={self.max_hz:g} (precisa 0 < min < max)"
            )


def profile_from_notes(notes: list[int]) -> Profile:
    """Perfil a partir das notas ouvidas: a faixa vai da mais grave à mais aguda."""
    heard = tuple(sorted(set(notes)))
    if not heard or heard[0] < COIL_LOW or heard[-1] > COIL_HIGH:
        raise ValueError(f"notas inválidas: use números MIDI de {COIL_LOW} a {COIL_HIGH}")
    if heard[-1] - heard[0] < 12:
        raise ValueError("marque notas cobrindo pelo menos 1 oitava (ex.: de C3 a C4)")
    hz = lambda p: 440 * 2 ** ((p - 69) / 12)
    # arredonda para fora, para as notas das pontas continuarem dentro da faixa
    low = math.floor(hz(heard[0]) * 100) / 100
    high = math.ceil(hz(heard[-1]) * 100) / 100
    return Profile(low, high, notes=heard)


def save_profile(profile: Profile, path: Path) -> None:
    path.write_text(
        json.dumps(asdict(profile), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_profile(path: Path) -> Profile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Profile(
            float(data["min_hz"]),
            float(data["max_hz"]),
            str(data.get("name", "plasma-speaker")),
            tuple(int(n) for n in data.get("notes", ())),
        )
    except FileNotFoundError:
        raise ValueError(f"perfil não encontrado: {path} (calibre no app)") from None
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"perfil inválido em {path}: {e!r}") from None
