#!/usr/bin/env python3
"""
Grafica las capturas del nodo NILM para visualizarlas rapidamente.

Lee el archivo JSON generado por capturar.py (header + array "datos") y genera
una figura con la forma de onda completa, un zoom de los primeros ciclos y,
opcionalmente, el espectro FFT.

Uso:
    python3 graficar.py                          # grafica TODAS las capturas de capturas/
    python3 graficar.py ruta/a/captura.json      # grafica un archivo especifico
    python3 graficar.py --dir otra_carpeta        # grafica todas las de otra carpeta
    python3 graficar.py --ultima                  # solo la captura mas reciente de capturas/
    python3 graficar.py --fft                     # incluye espectro FFT (0 - 500 Hz lineales)
    python3 graficar.py --fft-log                 # FFT con amplitud en dB
    python3 graficar.py --fft --fft-max-hz 2000   # FFT hasta 2000 Hz
    python3 graficar.py --solo-detalle --fft      # solo detalle temporal + FFT + recuadro
    python3 graficar.py --puntos                  # solo puntos, sin unir con linea
    python3 graficar.py --no-show                 # solo guarda PNG, no abre ventana
    python3 graficar.py --ultima --par            # superpone el canal companero (I con V) en el detalle

Archivos de tension (canal 1, sufijo _v.json) se grafican con sus unidades [V].

Dependencias: numpy, matplotlib.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import matplotlib
import matplotlib.pyplot as plt


def cargar(ruta):
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No se encontro el archivo: {ruta}")

    with open(ruta, "r", encoding="utf-8") as f:
        p = json.load(f)

    datos = p.get("datos")
    if not isinstance(datos, list) or len(datos) == 0:
        raise ValueError("El archivo no contiene un array 'datos' valido")

    return {
        "categoria": p.get("categoria", "N/A"),
        "lugar": p.get("lugar", "N/A"),
        "canal": int(p.get("canal", 0)),
        "magnitud": p.get("magnitud", "corriente"),
        "unidad": p.get("unidad", "A"),
        "fs": float(p.get("fs", 10000)),
        "duracion": float(p.get("duracion", 0)),
        "muestras": int(p.get("muestras", len(datos))),
        "k": p.get("k"),
        "formato": p.get("formato"),
        "bytes": p.get("bytes"),
        "datos": np.asarray(datos, dtype=float),
    }


def _recuadro_info(ax, texto):
    """Dibuja el recuadro con las metricas de la captura."""
    ax.text(0.015, 0.95, texto, transform=ax.transAxes, fontsize=9,
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white",
                      alpha=0.85, edgecolor="#cccccc"))


def ruta_par(ruta):
    """Ruta del canal companero de una captura: <base>.json <-> <base>_v.json."""
    base, ext = os.path.splitext(ruta)
    return base[:-2] + ext if base.endswith("_v") else base + "_v" + ext


def graficar(med, incluir_fft=False, ruta_salida=None, mostrar=True, solo_puntos=False,
             cerrar=True, fft_log=False, fft_max_hz=None, solo_detalle=False, par=None):
    senal = med["datos"]
    fs = med["fs"]
    n = len(senal)
    u = med["unidad"]
    nombre_mag = med["magnitud"].capitalize()
    etiqueta_y = f"{nombre_mag} [{u}]"

    t = np.arange(n) / fs

    # Metricas
    v_max = np.max(senal)
    v_min = np.min(senal)
    v_pp = v_max - v_min
    v_rms = np.sqrt(np.mean(senal ** 2))
    v_dc = np.mean(senal)

    info = (
        f"fs: {fs:,.0f} Hz\n"
        f"Muestras: {n:,}\n"
        f"Duracion: {t[-1]:.3f} s\n"
        f"Max: {v_max:+.4f} {u}\n"
        f"Min: {v_min:+.4f} {u}\n"
        f"Pico-Pico: {v_pp:.4f} {u}\n"
        f"RMS: {v_rms:.4f} {u}\n"
        f"Offset DC: {v_dc:+.4f} {u}"
    )

    # Paneles a mostrar (en orden). Con --solo-detalle se omite la senal completa.
    paneles = []
    if not solo_detalle:
        paneles.append("completa")
    paneles.append("detalle")
    if incluir_fft:
        paneles.append("fft")

    num_sub = len(paneles)
    fig, axes = plt.subplots(num_sub, 1, figsize=(12, 3.7 * num_sub), squeeze=False)
    ejes = {nombre: axes[i, 0] for i, nombre in enumerate(paneles)}

    # --- 1. Senal completa ---
    if "completa" in ejes:
        ax1 = ejes["completa"]
        if solo_puntos:
            ax1.plot(t * 1000.0, senal, color="#1f77b4", linestyle="none",
                     marker=".", markersize=1.5, label="Senal")
        else:
            ax1.plot(t * 1000.0, senal, color="#1f77b4", linewidth=1.0, label="Senal")
        ax1.axhline(v_rms, color="green", linestyle="--", alpha=0.7, label=f"RMS: {v_rms:.4f}")
        ax1.axhline(-v_rms, color="green", linestyle="--", alpha=0.7)
        ax1.axhline(0, color="black", linewidth=0.8, alpha=0.5)
        ax1.set_title(f"Captura NILM - Categoria: {med['categoria']} ({nombre_mag}, CH{med['canal']})",
                      fontsize=13, fontweight="bold")
        ax1.set_xlabel("Tiempo [ms]")
        ax1.set_ylabel(etiqueta_y)
        ax1.grid(True, linestyle=":", alpha=0.6)
        ax1.legend(loc="upper right")
        _recuadro_info(ax1, info)
    else:
        fig.suptitle(f"Captura NILM - Categoria: {med['categoria']} ({nombre_mag}, CH{med['canal']})",
                     fontsize=13, fontweight="bold")

    # --- 2. Zoom primeros 100 ms ---
    ax2 = ejes["detalle"]
    zoom_ms = min(100.0, t[-1] * 1000.0)
    n_zoom = int((zoom_ms / 1000.0) * fs)
    if solo_puntos:
        ax2.plot(t[:n_zoom] * 1000.0, senal[:n_zoom], color="#d62728", linestyle="none",
                 marker=".", markersize=4.0, label="Detalle")
    else:
        ax2.plot(t[:n_zoom] * 1000.0, senal[:n_zoom], color="#d62728", linewidth=1.0,
                 marker=".", markersize=2.0, label="Detalle")
    ax2.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax2.set_title(f"Detalle temporal (primeros {zoom_ms:.1f} ms)",
                  fontsize=11, fontweight="bold")
    ax2.set_xlabel("Tiempo [ms]")
    ax2.set_ylabel(etiqueta_y)
    ax2.grid(True, linestyle=":", alpha=0.6)
    # Canal companero (I con V o V con I) en eje secundario: muestras sincronizadas
    if par is not None and abs(par["fs"] - fs) < 1e-6:
        ax2b = ax2.twinx()
        n_par = min(n_zoom, len(par["datos"]))
        ax2b.plot(t[:n_par] * 1000.0, par["datos"][:n_par], color="#1f77b4", linewidth=1.0,
                  alpha=0.75, label=f"{par['magnitud'].capitalize()} (CH{par['canal']})")
        ax2b.set_ylabel(f"{par['magnitud'].capitalize()} [{par['unidad']}]")
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax2b.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="upper right")
    else:
        ax2.legend(loc="upper right")
    # En modo solo-detalle, el recuadro de informacion va en este panel.
    if "completa" not in ejes:
        _recuadro_info(ax2, info)

    # --- 3. FFT opcional ---
    if "fft" in ejes:
        ax3 = ejes["fft"]
        ventana = np.hanning(n)
        fft = np.fft.rfft((senal - v_dc) * ventana)
        mag = (np.abs(fft) / n) * 2
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)

        # Limite de frecuencia: el indicado, o el default segun el modo
        if fft_max_hz is not None:
            limite = float(fft_max_hz)
        elif fft_log:
            limite = 5000.0
        else:
            limite = 500.0
        limite = min(limite, fs / 2.0)  # nunca por encima de Nyquist
        mask = freqs <= limite

        if fft_log:
            # Amplitud en escala logaritmica (dB), eje de frecuencia lineal.
            mag_db = 20.0 * np.log10(np.maximum(mag[mask], 1e-12))
            ax3.plot(freqs[mask], mag_db, color="#2ca02c", linewidth=1.0)
            ax3.set_title(f"Espectro FFT (0 - {limite:.0f} Hz, amplitud en dB)",
                          fontsize=11, fontweight="bold")
            ax3.set_ylabel("Amplitud [dB]")
        else:
            ax3.plot(freqs[mask], mag[mask], color="#2ca02c", linewidth=1.1)
            ax3.set_title(f"Espectro FFT (0 - {limite:.0f} Hz)",
                          fontsize=11, fontweight="bold")
            ax3.set_ylabel(f"Amplitud [{u}]")
        ax3.set_xlabel("Frecuencia [Hz]")
        ax3.grid(True, linestyle=":", alpha=0.6)
        ax3.set_xlim(0, limite)

    if "completa" in ejes:
        plt.tight_layout()
    else:
        plt.tight_layout(rect=(0, 0, 1, 0.96))

    if ruta_salida:
        plt.savefig(ruta_salida, dpi=150)
        print(f"[+] Grafica guardada en: {ruta_salida}")

    if mostrar:
        try:
            if matplotlib.get_backend().lower() not in ("agg", "pdf", "ps", "svg"):
                plt.show()
        except Exception:
            pass

    if cerrar:
        plt.close(fig)


def capturas_en(directorio="capturas"):
    archivos = sorted(glob.glob(os.path.join(directorio, "*.json")))
    if not archivos:
        raise FileNotFoundError(f"No hay capturas JSON en '{directorio}'")
    return archivos


def main():
    ap = argparse.ArgumentParser(
        description="Graficar capturas del nodo NILM (por defecto, todas las de capturas/)")
    ap.add_argument("archivo", nargs="?", default=None,
                    help="ruta a un JSON puntual (si se omite, grafica todas las de capturas/)")
    ap.add_argument("--dir", default="capturas", help="directorio de capturas (default: capturas)")
    ap.add_argument("--ultima", action="store_true",
                    help="graficar solo la captura mas reciente del directorio (por timestamp del nombre)")
    ap.add_argument("--fft", action="store_true",
                    help="incluir espectro FFT (amplitud lineal)")
    ap.add_argument("--fft-log", action="store_true",
                    help="espectro FFT con amplitud en dB (eje de frecuencia lineal en Hz)")
    ap.add_argument("--fft-max-hz", type=float, default=None,
                    help="frecuencia maxima a graficar en la FFT (default: 500 Hz, o 5000 Hz con --fft-log)")
    ap.add_argument("--solo-detalle", action="store_true",
                    help="graficar solo el detalle temporal (+ FFT si se pide) con el recuadro de informacion")
    ap.add_argument("--puntos", action="store_true",
                    help="graficar solo los puntos (marcadores) sin unirlos con linea")
    ap.add_argument("--no-show", action="store_true", help="solo guardar PNG, no mostrar ventana")
    ap.add_argument("--par", action="store_true",
                    help="superponer el canal companero (<base>_v.json o <base>.json) en el detalle")
    args = ap.parse_args()

    incluir_fft = args.fft or args.fft_log

    # Un archivo puntual o todos los de la carpeta
    if args.archivo is not None:
        archivos = [args.archivo]
    else:
        try:
            archivos = capturas_en(args.dir)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
        if args.ultima:
            archivos = archivos[-1:]

    backend = matplotlib.get_backend().lower()
    interactivo = backend not in ("agg", "pdf", "ps", "svg") and not args.no_show

    print(f"Capturas a graficar: {len(archivos)}")

    graficadas = 0
    for ruta in archivos:
        print(f"Cargando: {ruta}")
        try:
            med = cargar(ruta)
        except Exception as e:
            print(f"[AVISO] Se omite {ruta}: {e}", file=sys.stderr)
            continue

        par = None
        if args.par and os.path.exists(ruta_par(ruta)):
            try:
                par = cargar(ruta_par(ruta))
            except Exception as e:
                print(f"[AVISO] No se pudo cargar el canal companero: {e}", file=sys.stderr)

        nombre_salida = os.path.splitext(ruta)[0] + ".png"
        # En lote no mostramos cada figura: se muestran todas juntas al final.
        graficar(med, incluir_fft=incluir_fft, ruta_salida=nombre_salida,
                 mostrar=False, solo_puntos=args.puntos, cerrar=not interactivo,
                 fft_log=args.fft_log, fft_max_hz=args.fft_max_hz,
                 solo_detalle=args.solo_detalle, par=par)
        graficadas += 1

    if interactivo and graficadas > 0:
        plt.show()
        plt.close("all")

    print(f"Listo. {graficadas} captura(s) graficada(s).")


if __name__ == "__main__":
    main()
