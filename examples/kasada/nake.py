from curl_cffi import requests





headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh_CN",
    "Anti-Headers": "{\"takeoff_place\":\"CAN\",\"arrive_place\":\"BJS\",\"is_roundtrip\":1,\"departure_date\":\"2026-10-15\",\"black_box\":\"iWPHo1791883796Yn6esmFXG2e\",\"channel\":\"9air_web\"}",
    "Cache-Control": "must-revalidate",
    "Connection": "keep-alive",
    "Expires": "0",
    "Pragma": "no-cache",
    "Referer": "https://9air.com/zh-CN/book/booking",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "sec-ch-ua": "\"Chromium\";v=\"154\", \"Google Chrome\";v=\"154\", \"Not A(Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\""
}
cookies = {
    "x-s3-sid": "S1g4Gr5p1u8jt34413ngyh7zd",
    "x-s3-s4e": "zeH7%2BRTmj0GgNFgXlme98qTFfsHijuPyeKKehq7ro9zcgV0HKJyNmlFI%2BUHzqrWxGujzfPsyiFe%2BOP%2F7BE2d7YoI7HPt%2BrzFnJwRxpr11CFMIgByWdxieGk9BkUxnhvUvOBEpRqvbSLsrlpz4xCCAEn0ujkQDZqFn9rnVB72AFTN1uG7DqcZYJ4Y90VQ23Q9lV%2BuXObaMscek8ZWHDxSCZI7z2Ire45A1uxoyPSPgyEou56TR2IgKgHL16MXGdFSf0YBY0I5GjpaNQyZZ1ILKNnaItdwK8X85Fx%2FZBm2d1bgfQcVCjOBSkpKQK2kxz4ioawaKuIB8fQLKm5b6O%2Fp2S7Ft2XXnsvpg2RRxjihj82hCDYxBxWXKM%2BDGgXx2dRmvMZ0xNxfgrrJXhHOjl4XQpGkOSTI7UzD1nQEZ%2BVZVxu6LwZxdShLcPZ4WMNviFBg%2B6WvjphSZrxU9DE3uCLj2FR6AZkeCA2S2AxPiESDwPZhyzQjupgRkDr55FoN1ImCMMkWyFAPfbZzwVobkd1U9H5K0hsgLbDUu%2Fa4R2iHCeiUHwS3zP8FWk4XZ8rNfnnM%2Bgf0XTejhTmK4qMuupMnElZIGlLm%2FdzeKcMwVI5y088%2FF6JrIi6%2BYqqvXfD4UMc%2BA4usXBmXPVEC4j5iyeFIOTlEEyD4CK52L6vWXlAVqSBLjLP7T4CVdA1kO47lsOFOEEKZBRb4R6AcN1IHUHL1y6OrL0X3TMMlcf1maT6%2F3fttxSaVQ65CpbTq%2BP40kDhGT0fhUzZ1Ro%2BXjp1vhhdGP%2BD3FhRqHxlXIQrkEqjouUFV5Ohlj5Wc1pPPBrBawG8q3sSsb0655e632eaf384fadf604ae3928bd81c0f9a74d:51:6ef8ea69-c6e2-11f1-b877-005056b92763:040e82202f",
    "x-s3-tid": "b0655e632eaf384fadf604ae3928bd81c0f9a74d:51:6ef8ea69-c6e2-11f1-b877-005056b92763:040e82202f",
    "Hm_lvt_e6ad07b64d9406bcdef2e6f89b081be2": "1791881143",
    "HMACCOUNT": "42015CFC7F62054C",
    "SESSION": "0a44b383-b51f-47d0-a7a5-70d1926c7912",
    "__snaker__id": "uDPHJutXlBwteXKe",
    "JSESSIONID": "e7f75eec-d914-470d-970c-b795a9fce4ea",
    "bookEndTime": "180000",
    "gdxidpyhxdE": "vr2e%2BuuwqlZiSqc7cceyhtw%2BBZD6Us6r4pD859tbSN5DkmA2n4wSESbRdc7lyCI0v9RHJ%5CU6O13s%5CHSplBSYUhERSLiKAAtYicmABQhTxD2xtO12%2FsvdmNKcVYOPjt1flt8y%2BEW%5CNKi3j1RLWf0hn8jNX6HABpzjJN4RGphh80MxpVv%5C%3A1791884567065",
    "Hm_lpvt_e6ad07b64d9406bcdef2e6f89b081be2": "1791883795",
    "bookStartTime": "17000"
}
url = 'https://9air.com/shop/api/shopping/b2c/searchflight'
params = {
    "language": "zh_CN",
    "currency": "CNY",
    "flightCondition": "index:0",
    "depCode:CAN": "",
    "arrCode:BJS": "",
    "depDate:2026-10-15": "",
    "depCodeType:CITY": "",
    "arrCodeType:CITY": "",
    "channelNo": "B2C",
    "tripType": "OW",
    "groupIndicator": "I",
    "adultCount": "1",
    "childCount": "0",
    "infantCount": "0",
    "airlineCode": "",
    "directType": "",
    "cabinClass": "",
    "taxFee": "",
    "taxCurrency": "",
    "promotionCode": ""
}
response = requests.get(url, headers=headers, cookies=cookies, params=params)

print(response.text)
print(response)