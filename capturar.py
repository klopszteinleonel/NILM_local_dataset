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
       Si se indican --equipo y --estado, se agregan a los dos archivos. No se envian al nodo.

La subida a MariaDB la realiza un script separado (no incluido aqui).

Uso:
    python3 capturar.py --categoria microondas --lugar cocina --duracion 1.0
    python3 capturar.py --categoria lavarropas --lugar lavadero --duracion 3.0 --host 192.168.100.29
    python3 capturar.py --categoria "aire,tele" --lugar living --duracion 1.0   # combinada real
    python3 capturar.py --categoria nada --lugar living --duracion 1.0          # sin carga
    python3 capturar.py --categoria aire --canal v                              # solo tension
    python3 capturar.py --categoria aire --canal iv                             # corriente + tension sincronizadas
    python3 capturar.py --categoria heladera --lugar casa3 --canal iv --equipo heladera_gafa --estado recien_arrancada
    python3 capturar.py --categoria "aire,luces" --lugar casa3 --canal iv --equipo "aire_ventana,lampara_led" --estado "frio,3_lamparas"
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


AYUDA_FINAL = """
equipo y estado:
  Sirven para saber despues que aparato se midio y que estaba haciendo. Sin esto
  no se puede reconstruir: dos capturas de "aire" pueden ser dos equipos distintos.

  --equipo  Nombre que inventas para ese aparato fisico. Usa SIEMPRE el mismo nombre
            para el mismo aparato, aunque lo midas otro dia o en otro lugar, y un
            nombre distinto para otro aparato de la misma categoria.
            Ejemplos: pava_negra, aire_ventana_local, heladera_gafa, lampara_led_living

  --estado  Que esta haciendo el aparato durante la captura.
            Ejemplos: recien_arrancada, estable, frio, calor, lavado, centrifugado,
            3_lamparas, tele_encendida, potencia_maxima

  Son opcionales, pero si faltan se muestra un aviso. Con --categoria nada no hacen falta.

capturas combinadas:
  Un valor por cada carga, separados por coma y en el MISMO orden que --categoria.
  Si un dato no se sabe o no aplica, poner un guion en esa posicion.
    --categoria "aire,luces" --equipo "aire_ventana,lampara_led" --estado "frio,-"

ejemplos:
  python3 capturar.py --categoria pava --lugar casa3 --canal iv --equipo pava_negra --estado estable
  python3 capturar.py --categoria heladera --lugar casa3 --canal iv --equipo heladera_gafa --estado recien_arrancada
  python3 capturar.py --categoria "aire,luces" --lugar casa3 --canal iv --equipo "aire_ventana,lampara_led" --estado "frio,3_lamparas"
  python3 capturar.py --categoria nada --lugar casa3 --canal iv

Solo se permiten letras sin acento, numeros, '_', '-', ',' y espacios.
"""


def validar_por_carga(nombre, valor, n_cargas):
    """Devuelve el texto listo para guardar. Exige un valor por carga, en el orden de --categoria."""
    partes = [p.strip() for p in valor.split(",")]
    if any(not p for p in partes):
        print(f"[ERROR] '--{nombre}' tiene un valor vacio. Si no se sabe o no aplica, usa un guion: "
              f"--{nombre} \"{','.join(p or '-' for p in partes)}\"", file=sys.stderr)
        sys.exit(1)
    if len(partes) != n_cargas:
        print(f"[ERROR] --categoria tiene {n_cargas} carga(s) pero --{nombre} tiene {len(partes)} valor(es). "
              "Tiene que haber uno por carga, en el mismo orden (guion si no aplica). Ver --help", file=sys.stderr)
        sys.exit(1)
    return ",".join(partes)


def agregar_metadatos(paquete, metadatos):
    """Devuelve el paquete con los metadatos antes de 'datos', para que se lean en el encabezado."""
    salida = {k: v for k, v in paquete.items() if k != "datos"}
    salida.update(metadatos)
    if "datos" in paquete:
        salida["datos"] = paquete["datos"]
    return salida


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
    ap = argparse.ArgumentParser(description="Comandar y guardar una captura del nodo NILM",
                                 epilog=AYUDA_FINAL, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--categoria", required=True, help="categoria de la medicion")
    ap.add_argument("--lugar", default="desconocido", help="lugar de la medicion")
    ap.add_argument("--equipo", help="nombre del aparato fisico medido; en combinadas, uno por carga separados "
                                     "por coma (explicado abajo)")
    ap.add_argument("--estado", help="que esta haciendo el aparato durante la captura; en combinadas, uno por "
                                     "carga separados por coma (explicado abajo)")
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
    for nombre, valor in (("categoria", args.categoria), ("lugar", args.lugar),
                          ("equipo", args.equipo or "-"), ("estado", args.estado or "-")):
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

    cargas = [c.strip() for c in categoria.split(",") if c.strip()]
    sin_carga = cargas == ["nada"]
    metadatos = {}
    for nombre, valor in (("equipo", args.equipo), ("estado", args.estado)):
        if valor is not None:
            metadatos[nombre] = validar_por_carga(nombre, valor, len(cargas))
        elif not sin_carga:
            print(f"[AVISO] Falta --{nombre}: esta captura no va a registrar ese dato. Ver --help", file=sys.stderr)

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
            json.dump(agregar_metadatos(paquete, metadatos), f)
        print(f"Guardado en: {ruta}")

    if metadatos:
        print("Registrado: " + ", ".join(f"{k}={v}" for k, v in metadatos.items()))


if __name__ == "__main__":
    main()
