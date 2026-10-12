from fastapi import FastAPI


app = FastAPI(title="api-health-monitor")
@app.get("/")
def root():
    return{
        "application":"API HEALTH MONITOR",
        "version":"0.1.0"
    }
@app.get("/health")
def health_check():
    return {"status": "working", "version": 1}
