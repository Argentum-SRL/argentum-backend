import httpx
from fastapi import HTTPException
from app.core.config import settings

def verify_google_token(token: str) -> dict:
    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
    response = httpx.get(url)
    
    if response.status_code != 200:
        print("Error de Google API:", response.text)
        raise HTTPException(status_code=400, detail="Token de Google inválido")
        
    token_data = response.json()
    
    if "aud" not in token_data or token_data["aud"] != settings.GOOGLE_CLIENT_ID:
        print("Error de aud:", token_data.get("aud"), "Esperado:", settings.GOOGLE_CLIENT_ID)
        raise HTTPException(status_code=400, detail="Audiencia del token no coincide")
        
    return token_data
