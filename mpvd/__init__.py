"""mpvd: companion daemon for MPV-UOS."""

from mpvd.config import use_bundled_ca_bundle

__version__ = "0.1.6"

# Windows: el intérprete del paquete no lee el almacén de certificados del sistema y trae muy pocas raíces, así que
# sin esto fallan por TLS el DNS-over-HTTPS del enlace (F2) y el propio túnel. Tiene que pasar antes de que nadie
# cree un contexto SSL; importar mpvd es lo primero que hace todo el mundo (mpvd, la sala, los tests).
use_bundled_ca_bundle()
