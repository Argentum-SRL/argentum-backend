import httpx
from app.core.config import settings

class StorageService:
    def __init__(self):
        self.url = f"{settings.SUPABASE_URL}/storage/v1/object"
        # Usamos la Service Role Key para tener permisos completos de escritura
        self.key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
        self.headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key
        }
        self.bucket = "perfiles"

    def subir_archivo(self, file_content: bytes, filename: str, content_type: str) -> str:
        """Sube un archivo a Supabase Storage y devuelve la URL pública."""
        upload_url = f"{self.url}/{self.bucket}/{filename}"
        
        with httpx.Client() as client:
            # Intentar subir el archivo
            resp = client.post(
                upload_url,
                content=file_content,
                headers={
                    **self.headers, 
                    "Content-Type": content_type,
                    "x-upsert": "true" # Esto permite sobrescribir si ya existe
                }
            )
            
            # Si el bucket no existe (404 en el path del bucket), intentamos avisar
            if resp.status_code == 404 and f"Bucket {self.bucket} not found" in resp.text:
                raise Exception(f"El bucket '{self.bucket}' no existe en Supabase Storage. Por favor créalo en el dashboard.")

            if resp.status_code not in [200, 201]:
                # Si falló por otra cosa, intentamos un PUT como fallback
                resp = client.put(
                    upload_url,
                    content=file_content,
                    headers={**self.headers, "Content-Type": content_type}
                )
                
            if resp.status_code not in [200, 201]:
                raise Exception(f"Error de Supabase ({resp.status_code}): {resp.text}")

        # Retornar la URL pública
        return f"{settings.SUPABASE_URL}/storage/v1/object/public/{self.bucket}/{filename}"

    def eliminar_archivo(self, filename: str):
        """Elimina un archivo de Supabase Storage."""
        delete_url = f"{self.url}/{self.bucket}/{filename}"
        with httpx.Client() as client:
            client.delete(delete_url, headers=self.headers)

storage_service = StorageService()
