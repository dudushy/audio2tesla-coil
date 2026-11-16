import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PATH = Path("device.json")


@dataclass(frozen=True)
class Profile:
    min_hz: float
    max_hz: float
    name: str = "plasma-speaker"

    def __post_init__(self):
        if not 0 < self.min_hz < self.max_hz:
            raise ValueError(
                f"faixa inválida: min={self.min_hz:g} max={self.max_hz:g} (precisa 0 < min < max)"
            )


def save_profile(profile: Profile, path: Path) -> None:
    path.write_text(
        json.dumps(asdict(profile), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_profile(path: Path) -> Profile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Profile(
            float(data["min_hz"]), float(data["max_hz"]), str(data.get("name", "plasma-speaker"))
        )
    except FileNotFoundError:
        raise ValueError(f"perfil não encontrado: {path} (calibre no app)") from None
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"perfil inválido em {path}: {e!r}") from None
