from curl_cffi import requests




headers = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "client-identifier": "168287ea-1201-45f6-9b45-5bbea49f8ee7",
    "client_id": "USBC",
    "content-type": "application/json",
    "locale": "en-US",
    "origin": "https://www.costco.com",
    "priority": "u=1, i",
    "referer": "https://www.costco.com/",
    "searchresultprovider": "GRS",
    "sec-ch-ua": "\"Chromium\";v=\"154\", \"Google Chrome\";v=\"154\", \"Not A(Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "x-kpsdk-cd": "{\"workTime\":1791884863077,\"id\":\"dfe7839845453115fa198ff46c5be7b4\",\"answers\":[5,1],\"duration\":31.4,\"d\":191,\"st\":1791884744227,\"rst\":1791884744418}",
    "x-kpsdk-ct": "0v6GXWGcbOkm9esGF8d0xxy1uEnuqvJnFjzFlb5xk40itkD6ZS0B0ejPsdq38Yal8OmwPU2RS7ZArZ61gxGer7mf5ryN0w7KyOW7C7Ae72jdHeFXSrIimQyG6FNRXAhYvr6tanqiTLHmxOj4TiJMazI6xvzYMfId",
    "x-kpsdk-h": "01LAYcQHBsrRxyq9ja+CNVxPZlQ9g=",
    "x-kpsdk-v": "j-1.2.864"
}
url = 'https://gdx-api.costco.com/catalog/search/api/v1/search'
data = {
    "visitorId": "89520265245636856490444489237282908088",
    "query": "iphone 18 pro",
    "pageSize": 24,
    "offset": 0,
    "orderBy": None,
    "searchMode": "page",
    "personalizationEnabled": True,
    "warehouseId": "1-wh",
    "shipToPostal": "98101",
    "shipToState": "WA",
    "deliveryLocations": [
        "115-bd",
        "1-wh",
        "1250-3pl",
        "1321-wm",
        "1456-3pl",
        "283-wm",
        "561-wm",
        "725-wm",
        "731-wm",
        "758-wm",
        "759-wm",
        "847_0-cor",
        "847_0-cwt",
        "847_0-edi",
        "847_0-ehs",
        "847_0-membership",
        "847_0-mpt",
        "847_0-spc",
        "847_0-wm",
        "847_1-cwt",
        "847_1-edi",
        "847_bosch_1474-edi",
        "847_d-fis",
        "847_ge_sea-edi",
        "847_lg_n1f-edi",
        "847_lux_us01-edi",
        "847_NA-cor",
        "847_NA-pharmacy",
        "847_NA-wm",
        "847_ss_u362-edi",
        "847_wp_r458-edi",
        "951-wm",
        "952-wm",
        "9847-wcs"
    ],
    "filterBy": [
        "HIDE_OUT_OF_STOCK"
    ],
    "pageCategories": []
}
response = requests.post(url, headers=headers, json=data)

print(response.text)
print(response)