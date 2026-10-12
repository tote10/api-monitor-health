import requests
url="https://httpbin.org/get"
response = requests.get(url,timeout=5)
print(response.status_code)