import json

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field, HttpUrl


app = FastAPI(title="API Health Monitor")


##temporary storagr replaced by postgresql database latter
endpoints : dict[int,dict]={}
next_endepoint_id = 1

class EndpointCreate(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    url:HttpUrl
    Interval_seconds:int=Field(default=60,gt=0,le=3600)
class EndPointResponse(BaseModel):
    id:int
    name:str
    url:HttpUrl
    Interval_seconds:int
    is_active:bool

@app.post("/api/v1/endpoints", response_model=EndPointResponse, status_code=status.HTTP_201_CREATED)
def register_endpoint(data: EndpointCreate):
    global next_endepoint_id
    normalized_url = str(data.url).rstrip('/')
    for endpoint in endpoints.values():
        if endpoint['url'].rstrip('/') == normalized_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Endpoint with the same URL already exists.")
    #create and store the endpoint
    endpoint={
        "id":next_endepoint_id,
        "name":data.name,
        "url": normalized_url,
        "Interval_seconds":data.Interval_seconds,
        "is_active":True
    }
    endpoints[next_endepoint_id]=endpoint
    next_endepoint_id+=1
    return endpoint
@app.get("/api/v1/endpoints", response_model=list[EndPointResponse])
def list_endpoints():
    # Materialize the dictionary values     so the response is JSON serializable.
    return Response(
        content=json.dumps(list(endpoints.values()), indent=2),
        media_type="application/json",
    )