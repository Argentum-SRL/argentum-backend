import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self):
        self.supabase_url = settings.SUPABASE_URL.rstrip("/") if settings.SUPABASE_URL else ""
        self.key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY or ""
        self.url = f"{self.supabase_url}/storage/v1/object"
        self.bucket = "perfiles"
        self.headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key
        }

    def _asegurar_bucket(self, client: httpx.Client) -> None:
        """Verifica y crea el bucket público si no existe."""
        if not self.supabase_url or not self.key:
            return
        try:
            resp = client.post(
                f"{self.supabase_url}/storage/v1/bucket",
                json={"id": self.bucket, "name": self.bucket, "public": True},
                headers=self.headers
            )
            if resp.status_code in [200, 201]:
                logger.info(f"Bucket '{self.bucket}' creado exitosamente en Supabase Storage.")
        except Exception as e:
            logger.warning(f"No se pudo verificar/crear el bucket '{self.bucket}': {e}")

    def esta_disponible(self) -> bool:
        """Retorna True si Supabase Storage está configurado."""
        return bool(self.supabase_url and self.key)

    def subir_archivo(self, file_content: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        """Sube un archivo a Supabase Storage y devuelve la URL pública permanente."""
        if not self.esta_disponible():
            raise RuntimeError("Supabase Storage no está configurado.")

        upload_url = f"{self.url}/{self.bucket}/{filename}"
        
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)) as client:
            resp = client.post(
                upload_url,
                content=file_content,
                headers={
                    **self.headers, 
                    "Content-Type": content_type,
                    "x-upsert": "true"
                }
            )
            
            # Si el bucket no existe, intentamos crearlo y reintentar
            if resp.status_code in (400, 404) and "not found" in resp.text.lower():
                self._asegurar_bucket(client)
                resp = client.post(
                    upload_url,
                    content=file_content,
                    headers={
                        **self.headers, 
                        "Content-Type": content_type,
                        "x-upsert": "true"
                    }
                )

            if resp.status_code not in [200, 201]:
                # Fallback con PUT
                resp = client.put(
                    upload_url,
                    content=file_content,
                    headers={**self.headers, "Content-Type": content_type}
                )
                
            if resp.status_code not in [200, 201]:
                raise Exception(f"Error de Supabase Storage ({resp.status_code}): {resp.text}")

        # Retornar la URL pública permanente
        return f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{filename}"

    def eliminar_archivo(self, filename: str) -> None:
        """Elimina un archivo de Supabase Storage."""
        if not self.esta_disponible():
            return
        # Extraer solo el nombre de archivo si viene una URL completa
        clean_name = filename.split("/")[-1].split("?")[0]
        delete_url = f"{self.url}/{self.bucket}/{clean_name}"
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)) as client:
                client.delete(delete_url, headers=self.headers)
        except Exception as e:
            logger.warning(f"Error al eliminar {clean_name} de Supabase Storage: {e}")

storage_service = StorageService()
