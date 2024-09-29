# Define the request headers
$headers = @{}

$headers.Add('Content-Type', 'application/json')

# Add or update headers
$headers.Add('Authorization', '79931E79-1F7C-63E8-725F-171D236C5236')

# Define the request URL
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-01-02&subscriptionid=b7f47f43-6fd8-4365-a85c-c2a796375454&itemcount_singlepage=2000&page_num=1'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-01-20&subscriptionid=50b242f0-c351-492b-a899-5fd770b03ed2&itemcount_singlepage=2000&page_num=1'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-01-20&subscriptionid=58aa6c07-2c69-4922-9196-19aed3771a8e&itemcount_singlepage=2000&page_num=1'

#$url = 'http://localhost:7071/api/analysis_cost_main?date=2023-12&billingAccountid=59285102&itemcount_singlepage=16000&page_num=1'
#$url = 'http://localhost:7071/api/init_month?month=2023-12&billingAccountid=59285102'
#$url = 'http://localhost:7071/api/init_day?date=2024-2-3&billingAccountid=59285102'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-2-3&billingAccountid=59285102&itemcount_singlepage=16000&page_num=1'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-2-3&billingAccountid=59285102&itemcount_singlepage=16000'

#$url = 'https://analyzeazurecostds.azurewebsites.net/api/init_day?date=2024-2-4&billingAccountid=59285102'
#$url = 'https://analyzeazurecostds.azurewebsites.net/api/analysis_cost_main?date=2024-03-31&billingAccountid=59285102&itemcount_singlepage=1000&page_num=1'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-02&billingAccountid=59285102&itemcount_singlepage=1000&page_num=1'
#$url = 'https://analyzeazurecostds.azurewebsites.net/api/init_day?billingAccountid=59285102&date=2024-3-30&itemcount_singlepage=1000'


#local test
#$url = 'http://localhost:7071/api/Init_month_msft_bill?billingAccountid=59285102'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-5&billingAccountid=59285102&itemcount_singlepage=16000&page_num=1'
#$url = 'http://localhost:7071/api/analysis_cost_main?date=2024-05-01&billingAccountid=59285102&itemcount_singlepage=1000&page_num=1'
#$url = 'http://localhost:7071/api/init_day?date=2024-9-28&billingAccountid=59285102'

#Prod
$url = 'https://analyzeazurecostds.azurewebsites.net/api/analysis_cost_main?date=2024-09-28&billingAccountid=59285102&itemcount_singlepage=1000&page_num=1'
#$url = 'https://analyzeazurecostds.azurewebsites.net/api/init_day?date=2024-9-28&billingAccountid=59285102'

$response = Invoke-RestMethod -Uri $url -Method 'GET' -Headers $headers

$filePath = ".\test_outcome.json"

# Construct and send the request
$responseJson = $response | ConvertTo-Json
$responseJson | Set-Content -Path $filePath -Encoding UTF8




