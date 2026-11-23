import uvicorn, os
from dotenv import load_dotenv

load_dotenv()
if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        workers=1,   # MUST stay 1: in-memory process manager
    )