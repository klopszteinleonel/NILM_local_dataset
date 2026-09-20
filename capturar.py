#!/usr/bin/env python3
"""
Cliente TCP para comandar y guardar capturas del nodo NILM.

Flujo:
    1. Se conecta al nodo (servidor TCP local, por IP o mDNS).
    2. Envia el comando JSON: {"categoria": "...", "lugar": "...", "duracion": <segundos>,
       "canal": "i" | "v" | "iv"}.
    3. Recibe el ACK "OK".
    4. Recibe un paquete JSON por canal (header + datos anidados); con "iv" llegan
       dos objetos (corriente y tension, muestreados en el mismo tick) separados por '\n'.
    5. Los guarda en archivos locales:
         corriente: <directorio>/<timestamp>_<categoria>.json      (formato historico)
         tension:   <directorio>/<timestamp>_<categoria>_v.json

La subida a MariaDB la realiza un script separado (no incluido aqui).

Uso:
    python3 capturar.py --categoria microondas --lugar cocina --duracion 1.0
    python3 capturar.py --categoria lavarropas --lugar lavadero --duracion 3.0 --host 192.168.100.29
    python3 capturar.py --categoria "aire,tele" --lugar living --duracion 1.0   # combinada real
    python3 capturar.py --categoria nada --lugar living --duracion 1.0          # sin carga
    python3 capturar.py --categoria aire --canal v                              # solo tension
    python3 capturar.py --categoria aire --canal iv                             # corriente + tension sincronizadas
"""

import argparse
import json
import os
import socket
import sys
import time


def conectar(host, port):
    try:
        s = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        print(f"[ERROR] No se pudo conectar a {host}:{port}: {e}", file=sys.stderr)
        sys.exit(1)
    s.settimeout(90)
    return s


def leer_ack(s):
    """Lee una linea terminada en '\\n' (el ACK del nodo)."""
    ack = b""
    while not ack.endswith(b"\n"):
        ch = s.recv(1)
        if not ch:
            break
        ack += ch
    return ack.strip()


def decodificar_paquetes(buf):
    """Decodifica uno o mas objetos JSON concatenados (separados por espacios o '\n')."""
    texto = buf.decode("utf-8")
    decoder = json.JSONDecoder()
    paquetes = []
    pos = 0
    while True:
        while pos < len(texto) and texto[pos].isspace():
            pos += 1
        if pos >= len(texto):
            break
        try:
            obj, pos = decoder.raw_decode(texto, pos)
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON invalido en el paquete {len(paquetes) + 1}: {e}", file=sys.stderr)
            break
        paquetes.append(obj)
    return paquetes


def main():
    ap = argparse.ArgumentParser(description="Comandar y guardar una captura del nodo NILM")
    ap.add_argument("--categoria", required=True, help="categoria de la medicion")
    ap.add_argument("--lugar", default="desconocido", help="lugar de la medicion")
    ap.add_argument("--duracion", type=float, default=1.0, help="duracion en segundos")
    ap.add_argument("--canal", choices=("i", "v", "iv"), default="i",
                    help="i = corriente (CH0), v = tension (CH1), iv = ambas sincronizadas")
    ap.add_argument("--host", default="nodo-nilm.local", help="host del nodo (mDNS o IP)")
    ap.add_argument("--port", type=int, default=5000, help="puerto TCP del nodo")
    ap.add_argument("--outdir", default="capturas", help="directorio de salida")
    args = ap.parse_args()

    # Validar categoria y lugar (solo caracteres seguros para evitar JSON roto)
    # La coma separa clases en capturas combinadas: --categoria "aire,tele"
    caracteres_validos = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ,"
    for nombre, valor in (("categoria", args.categoria), ("lugar", args.lugar)):
        valor = valor.strip()
        if not valor:
            print(f"[ERROR] El campo '{nombre}' no puede estar vacio", file=sys.stderr)
            sys.exit(1)
        if any(c not in caracteres_validos for c in valor):
            print(f"[ERROR] '{nombre}' solo puede contener letras, numeros, '_', '-', ',' y espacios",
                  file=sys.stderr)
            sys.exit(1)

    categoria = args.categoria.strip()
    lugar = args.lugar.strip()

    duracion = args.duracion
    if duracion <= 0:
        print("[ERROR] La duracion debe ser mayor a 0", file=sys.stderr)
        sys.exit(1)

    comando = {"categoria": categoria, "lugar": lugar, "duracion": duracion, "canal": args.canal}

    print(f"Conectando a {args.host}:{args.port} ...")
    s = conectar(args.host, args.port)

    print(f"Enviando comando: {json.dumps(comando)}")
    s.sendall((json.dumps(comando) + "\n").encode())

    ack = leer_ack(s)
    if ack != b"OK":
        print(f"[ERROR] ACK inesperado: {ack!r}", file=sys.stderr)
        s.close()
        sys.exit(1)
    print("ACK recibido: OK")

    print("Midiendo y recibiendo datos ...")
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()

    if not buf:
        print("[ERROR] No se recibieron datos", file=sys.stderr)
        sys.exit(1)

    paquetes = decodificar_paquetes(buf)
    if not paquetes:
        print("[ERROR] La respuesta no contiene JSON valido", file=sys.stderr)
        sys.exit(1)

    esperados = len(args.canal)
    if len(paquetes) != esperados:
        print(f"[AVISO] Se esperaban {esperados} paquete(s) y llegaron {len(paquetes)}", file=sys.stderr)

    os.makedirs(args.outdir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = f"{ts}_{categoria.replace(' ', '_').replace(',', '+')}"

    for paquete in paquetes:
        muestras = paquete.get("muestras", 0)
        datos = paquete.get("datos", [])
        magnitud = paquete.get("magnitud", "corriente")
        print(f"Recibido: categoria={paquete.get('categoria')} lugar={paquete.get('lugar')} "
              f"canal={paquete.get('canal', 0)} ({magnitud}, {paquete.get('unidad', 'A')}) "
              f"fs={paquete.get('fs')} duracion={paquete.get('duracion')} "
              f"muestras={muestras} datos={len(datos)} "
              f"k={paquete.get('k')} bytes={paquete.get('bytes')}")

        if len(datos) != muestras:
            print(f"[AVISO] La cantidad de datos ({len(datos)}) no coincide con 'muestras' ({muestras})",
                  file=sys.stderr)

        sufijo = "_v" if magnitud == "tension" else ""
        ruta = os.path.join(args.outdir, f"{base}{sufijo}.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(paquete, f)
        print(f"Guardado en: {ruta}")


if __name__ == "__main__":
    main()
