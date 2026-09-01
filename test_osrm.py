import requests

def get_walking_distance(lat1, lon1, lat2, lon2):
    url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data['code'] == 'Ok':
                return data['routes'][0]['distance'] # in meters
    except Exception as e:
        print("Error", e)
    return None

dist = get_walking_distance(45.75, 4.85, 45.76, 4.86)
print(dist)
