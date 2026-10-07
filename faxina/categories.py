"""Classificação de arquivos por tipo e detecção de "lixo de desenvolvimento"."""
from __future__ import annotations

import os

# (id, rótulo, cor). A ordem define o índice usado nos arrays do scanner.
CATEGORIES = [
    ("video", "Vídeos", "#FF5B35"),
    ("image", "Imagens", "#28B59D"),
    ("audio", "Áudio", "#7467F0"),
    ("doc", "Documentos", "#E7B83C"),
    ("archive", "Compactados", "#D06F9E"),
    ("program", "Programas e instaladores", "#4B6CB7"),
    ("dev", "Código e dev", "#9FBC39"),
    ("other", "Outros", "#858B90"),
]
NCAT = len(CATEGORIES)
OTHER = NCAT - 1

_EXTENSIONS = {
    "video": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts vob mts m2ts",
    "image": "jpg jpeg png gif bmp tif tiff webp heic heif raw cr2 cr3 nef arw dng psd svg ico avif",
    "audio": "mp3 wav flac aac ogg m4a wma opus aiff aif mid midi",
    "doc": "pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf md csv epub mobi pages key numbers",
    "archive": "zip rar 7z tar gz tgz bz2 xz zst lz cab",
    "program": "exe msi dmg pkg deb rpm iso img appimage apk msix appx vhd vhdx vmdk dll",
    "dev": "py js mjs cjs ts tsx jsx java class jar c cpp h hpp cs go rs rb php html css scss json xml yml yaml "
           "sql sh bat ps1 ipynb kt swift dart lock map pyc pyd whl node so o a lib obj pdb",
}
EXT_TO_CAT: dict[str, int] = {}
for _index, (_cat, _label, _color) in enumerate(CATEGORIES):
    for _ext in _EXTENSIONS.get(_cat, "").split():
        EXT_TO_CAT["." + _ext] = _index


def extension(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def category_of(name: str) -> int:
    return EXT_TO_CAT.get(extension(name), OTHER)


# Arquivos que só ocupam espaço: temporários, downloads incompletos, caches do sistema.
TEMP_EXT = {".tmp", ".temp", ".crdownload", ".part", ".partial", ".download", ".dmp"}
TEMP_NAMES = {"thumbs.db", ".ds_store"}
# Instaladores e imagens de disco que normalmente só servem uma vez.
INSTALLER_EXT = {".msi", ".dmg", ".pkg", ".deb", ".rpm", ".iso", ".msix", ".appx"}
DOWNLOAD_DIRS = {"downloads", "transferências", "transferencias"}


def is_temp(name: str) -> bool:
    low = name.lower()
    return extension(low) in TEMP_EXT or low in TEMP_NAMES or low.startswith("~$")


# Pastas que dá para recriar com um comando. Cada entrada: (rótulo, como recriar).
JUNK_INFO = {
    "node_modules": ("Dependências do Node.js", "npm install"),
    "venv": ("Ambiente virtual do Python", "python -m venv .venv && pip install -r requirements.txt"),
    "__pycache__": ("Cache do Python", "recriado sozinho ao rodar o código"),
    "target": ("Build do Maven/Cargo", "mvn package ou cargo build"),
    "gradle-build": ("Build do Gradle", "gradle build"),
    ".gradle": ("Cache do Gradle", "recriado no próximo build"),
    ".next": ("Build do Next.js", "npm run build"),
    ".angular": ("Cache do Angular CLI", "recriado no próximo ng serve ou ng build"),
    "tool-cache": ("Cache de ferramenta Python", "recriado sozinho"),
}


def dev_junk_kind(name: str, parent: str, path: str) -> str | None:
    """Reconhece pastas regeneráveis. Quando o nome é ambíguo, exige um arquivo-marcador no projeto."""
    n = name.lower()
    if n == "node_modules":
        return "node_modules"
    if n == "__pycache__":
        return "__pycache__"
    if n in (".pytest_cache", ".mypy_cache", ".ruff_cache"):
        return "tool-cache"
    if n in (".venv", "venv", "env", ".env") and os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        return "venv"
    if n == "target" and (os.path.isfile(os.path.join(parent, "Cargo.toml")) or os.path.isfile(os.path.join(parent, "pom.xml"))):
        return "target"
    if n == "build" and (os.path.isfile(os.path.join(parent, "build.gradle")) or os.path.isfile(os.path.join(parent, "build.gradle.kts"))):
        return "gradle-build"
    if n == ".gradle":
        return ".gradle"
    if n == ".next" and os.path.isfile(os.path.join(parent, "package.json")):
        return ".next"
    if n == ".angular" and os.path.isfile(os.path.join(parent, "angular.json")):
        return ".angular"
    return None
