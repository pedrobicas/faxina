"""Monta uma "pasta pessoal" falsa com casos conhecidos para os testes."""
import os
import time


def write(path, size=0, content=None, age_days=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content if content is not None else os.urandom(size))
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))


def build_sample(root):
    same = os.urandom(300_000)
    write(os.path.join(root, "Videos", "ferias.mp4"), content=same)
    write(os.path.join(root, "Downloads", "ferias (1).mp4"), content=same)        # duplicado
    write(os.path.join(root, "Backup", "ferias-copia.mp4"), content=same)         # duplicado
    prefix = os.urandom(70_000)
    write(os.path.join(root, "Docs", "a.bin"), content=prefix + b"A" * 30_000)   # mesmo começo, fim diferente
    write(os.path.join(root, "Docs", "b.bin"), content=prefix + b"B" * 30_000)
    write(os.path.join(root, "Docs", "relatorio.pdf"), size=50_000)
    write(os.path.join(root, "Downloads", "setup-antigo.exe"), size=40_000, age_days=90)
    write(os.path.join(root, "Downloads", "ubuntu.iso"), size=60_000, age_days=120)
    write(os.path.join(root, "Downloads", "novo.exe"), size=10_000)               # recente: não sugere
    write(os.path.join(root, "Downloads", "filme.mkv.part"), size=5_000)          # download incompleto
    write(os.path.join(root, "Projetos", "site", "package.json"), content=b"{}", age_days=200)
    write(os.path.join(root, "Projetos", "site", "node_modules", "lib", "index.js"), size=80_000)
    write(os.path.join(root, "Projetos", "site", "node_modules", "lib", "node_modules", "x.js"), size=20_000)
    write(os.path.join(root, "Projetos", "api", "pom.xml"), content=b"<project/>")
    write(os.path.join(root, "Projetos", "api", "target", "app.jar"), size=30_000)
    write(os.path.join(root, "Projetos", "api", "target", "temp.tmp"), size=1_000)  # dentro de pasta de build: não duplica sugestão
    write(os.path.join(root, "Projetos", "py", ".venv", "pyvenv.cfg"), content=b"home = /usr")
    write(os.path.join(root, "Projetos", "py", ".venv", "lib", "x.py"), size=15_000)
    write(os.path.join(root, "Projetos", "misc", "target", "notes.txt"), size=500)  # "target" sem pom/Cargo: não é lixo
    write(os.path.join(root, ".faxina-quarentena", "lote", "velho.bin"), size=9_000)  # quarentena de outro disco: ignorada
    return root
