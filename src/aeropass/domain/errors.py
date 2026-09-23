"""Domain errors. Each subclass maps 1:1 to an ``Error.codigo`` in contracts/openapi.yaml."""

from typing import Any, ClassVar


class DomainError(Exception):
    codigo: ClassVar[str] = "ERROR"
    http_status: ClassVar[int] = 400
    mensaje_por_defecto: ClassVar[str] = "Error"

    def __init__(
        self,
        mensaje: str | None = None,
        detalles: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.mensaje = mensaje or self.mensaje_por_defecto
        self.detalles = detalles
        self.retry_after = retry_after
        super().__init__(self.mensaje)


class NoAutenticado(DomainError):
    codigo = "NO_AUTENTICADO"
    http_status = 401
    mensaje_por_defecto = "Token ausente, inválido o expirado"


class DatosInvalidos(DomainError):
    codigo = "DATOS_INVALIDOS"
    http_status = 422
    mensaje_por_defecto = "Datos inválidos"


class DocumentoVencido(DomainError):
    codigo = "DOCUMENTO_VENCIDO"
    http_status = 422
    mensaje_por_defecto = "El documento está vencido"


class DocumentoVencidoParaPase(DocumentoVencido):
    """Same code, but on pass emission the contract answers 403 (the request itself is valid)."""

    http_status = 403


class DocumentoYaRegistrado(DomainError):
    codigo = "DOCUMENTO_YA_REGISTRADO"
    http_status = 409
    mensaje_por_defecto = "El documento ya está registrado por otra cuenta"


class CuentaYaRegistrada(DomainError):
    codigo = "CUENTA_YA_REGISTRADA"
    http_status = 409
    mensaje_por_defecto = "La cuenta ya registró un documento distinto"


class PasajeroNoRegistrado(DomainError):
    codigo = "PASAJERO_NO_REGISTRADO"
    http_status = 404
    mensaje_por_defecto = "La cuenta aún no registró su documento"


class EstadoNoPermiteVerificacion(DomainError):
    codigo = "ESTADO_NO_PERMITE_VERIFICACION"
    http_status = 409
    mensaje_por_defecto = "El estado del pasajero no admite nuevas verificaciones"


class ImagenDemasiadoGrande(DomainError):
    codigo = "IMAGEN_DEMASIADO_GRANDE"
    http_status = 413
    mensaje_por_defecto = "La imagen supera el tamaño máximo de 4 MB"


class FormatoNoAdmitido(DomainError):
    codigo = "FORMATO_NO_ADMITIDO"
    http_status = 415
    mensaje_por_defecto = "Formato de imagen no admitido (JPEG, PNG o WebP)"


class AlmacenamientoNoDisponible(DomainError):
    codigo = "ALMACENAMIENTO_NO_DISPONIBLE"
    http_status = 503
    mensaje_por_defecto = "El almacenamiento no está disponible, reintenta más tarde"


class IdentidadNoActiva(DomainError):
    codigo = "IDENTIDAD_NO_ACTIVA"
    http_status = 403
    mensaje_por_defecto = "El pasajero no tiene una identidad digital activa"


class LimiteEmisionExcedido(DomainError):
    codigo = "LIMITE_EMISION_EXCEDIDO"
    http_status = 429
    mensaje_por_defecto = "Se superó el límite de emisiones por minuto"


class CredencialNoEncontrada(DomainError):
    codigo = "CREDENCIAL_NO_ENCONTRADA"
    http_status = 404
    mensaje_por_defecto = "La credencial no existe"
