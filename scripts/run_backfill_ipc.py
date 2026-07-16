import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not found in environment")
    sys.exit(1)

engine = create_engine(db_url)
Session = sessionmaker(bind=engine)
db = Session()

try:
    from app.services.tools_service import ejecutar_backfill_ipc
    print("Iniciando backfill de IPC...")
    resultado = ejecutar_backfill_ipc(db)
    print("Resultado:", resultado)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    db.close()
