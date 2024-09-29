import azure.functions as func
import logging
import json
import pandas as pd
import math
import numpy as np
import re
import csv
import requests
from datetime import datetime, timedelta
from analyze_cost_functions import save_oneday_to_csv
from analyze_cost_functions import blob_file_exists
from analyze_cost_functions import load_oneday_from_csv_with_adjustment
from analyze_cost_functions import load_oneday_from_api
from analyze_cost_functions import calculate_adjustment
from analyze_cost_functions import is_valid_subscription_id
from analyze_cost_functions import load_onemonth_from_csv_with_adjustment
from analyze_cost_functions import load_onemonth_from_csv_without_adjustment
from analyze_cost_functions import load_onemonth_from_csv_only_adjustment
from analyze_cost_functions import load_onemonth_from_api
from analyze_cost_functions import save_onemonth_msft_bill_to_csv
from analyze_cost_functions import CustomException429
from analyze_cost_functions import load_onemonth_msft_bill_from_csv


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
@app.route(route="analysis_cost_main", methods=['GET'])
def analysis_cost_main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info(f'start analysis_cost_main at {datetime.now()}')
    
    # -1. 检查 token
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    if (not istest) and (req.headers.get("Authorization") != config_data.get("prod_token")):
        token = req.headers.get("Authorization")
        return func.HttpResponse("show me the secret", status_code=401)

    date_input_str = req.params.get('date', '')
    if not date_input_str:
        return func.HttpResponse("Invalid parameter: date", status_code=400)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    if not billingAccount_id:
        pattern = r'^\d{4}-(0[1-9]|1[0-2])$'
        if re.match(pattern, date_input_str):
            return get_month_cost(req=req)
        
        pattern = r'^\d{4}-(0?[1-9]|1[0-2])-(0?[1-9]|[1-2]\d|3[01])$'
        if re.match(pattern, date_input_str):
            return get_day_cost(req=req)
    else:
        pattern = r'^\d{4}-(0?[1-9]|1[0-2])$'
        if re.match(pattern, date_input_str):
            logging.info(f'complete analysis_cost_main, return EA month cost at {datetime.now()}')
            return get_month_msft_bill_for_EA(req=req)
        
        pattern = r'^\d{4}-(0?[1-9]|1[0-2])-(0?[1-9]|[1-2]\d|3[01])$'
        if re.match(pattern, date_input_str):
            logging.info(f'complete analysis_cost_main, return EA day cost at {datetime.now()}')
            return get_day_cost_for_EA(req=req)
            
    return func.HttpResponse("Invalid parameter: date", status_code=400)


def convert_int64_to_int(obj):
    if isinstance(obj, list):
        return [convert_int64_to_int(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_int64_to_int(value) for key, value in obj.items()}
    elif isinstance(obj, np.int64):
        return int(obj)
    else:
        return obj
    

def get_day_cost(req: func.HttpRequest) -> func.HttpResponse:
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    

    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    return_df = pd.DataFrame()
    consumption_date = datetime.now()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    if istest: 
        try:
            consumption_date = datetime.strptime(config_data.get("test_consumption_date"), "%Y-%m-%d")
            subscription_id = config_data.get("test_subscription_id")
            itemcount_singlepage = int(config_data.get("test_itemcount_singlepage"))
            if not itemcount_singlepage or not consumption_date or not subscription_id:
                return func.HttpResponse("bad confiugration file: config.json", status_code=500)
        except:
            return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    else:
        try:
            consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m-%d")  
        except ValueError:
            return func.HttpResponse("Invalid date format", status_code=400)

        if not req.params.get('subscriptionid', ''):
            return func.HttpResponse("Invalid parameter: subscription id", status_code=400)
        if not is_valid_subscription_id(req.params.get('subscriptionid', '')):
            return func.HttpResponse("Invalid parameter: subscription id", status_code=400)
        subscription_id = req.params.get('subscriptionid', '')
        container_name = subscription_id

        if not req.params.get('itemcount_singlepage', ''):
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        try:
            itemcount_singlepage = int(req.params.get('itemcount_singlepage', ''))
        except ValueError:
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        
    # 1. 检查调用日的 年月日.csv 已经存在，则直接 加载 年月日.csv 及 年月日_adjustment.csv，返回
    date_str = consumption_date.strftime('%Y-%m-%d')
    month_str = consumption_date.strftime('%Y-%m')
    csv_filename = f'{month_str}/{date_str}.csv'
    adjustment_filename = f'{month_str}/{date_str}_adjustment.csv'
    if blob_file_exists(storageaccount_name, storageaccount_key, subscription_id, csv_filename):
        return_df = load_oneday_from_csv_with_adjustment(
            storageaccount_name, 
            storageaccount_key, 
            subscription_id, 
            csv_filename, 
            adjustment_filename)
        return_df.fillna("")
    else:
        #   更新，如果不存在，直接报错
        return func.HttpResponse(f"day CSV file not exist: {consumption_date}", status_code=500)

        #    更新，不在判断 CSV 不存在的情况。
        """
        # 2. 如果 年月日.csv 不存在，则开始编制 年月日.csv
        #    按照日期要求，获取 所有 usage details 保存在 consumption_df 只获取分摊数据 - AMORTIZED_COST_METRIC_TYPE
        try:
            consumption_df = load_oneday_from_api(client_id, client_secret, tenant_id, subscription_id, base_url, consumption_date)
            consumption_df.fillna("")
        except CustomException429 as e:
            return func.HttpResponse(str(e), status_code=429)

        # 3. 把这一天的数据保存在 CSV （客户请求的这一天）
        if not save_oneday_to_csv(storageaccount_name, storageaccount_key, container_name, consumption_df, consumption_date, False):
            return func.HttpResponse("save_oneday_to_csv failed", status_code=500);

        # 4. 开始计算上个月的 adjustment； 
        #    重新从 ARM_consumption_api 加载上月的数据 至 lastmonth_consumption_df
        #    加载 本月所有 adjustment.csv 内容至 lastmonth_adjustment_df
        #    逐个资源，使用 lastmonth_consumption_df 中的总价，减去 saved_df 中的总价  再减去 lastmonth_adjustment_df 中的总价，获得该资源今日 adjustment 数据，保存至 年月日_adjustment_df；
        #    如果有 年月日_adjustment_df 不为空，则保存在 storage account\年月日adjustment.csv；如果文件已经存在，则覆盖
        """
        
        #   更新，不在判断 adjustment
        """
        #    判断是否需要计算 adjustment
        if (consumption_date.year == datetime.now().year and 
            consumption_date.month == datetime.now().month and 
            consumption_date.day < 8 ):
            # 如果输入日期是本月的1-7日，则计算 adjustment
            try:
                consumption_adjustment_df = calculate_adjustment(
                    client_id=client_id, 
                    client_secret=client_secret,
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    base_url=base_url,
                    storageaccount_name=storageaccount_name,
                    storageaccount_key=storageaccount_key,
                    container_name=subscription_id,
                    save_date=consumption_date)
                consumption_adjustment_df.fillna("")
            except CustomException429 as e:
                return func.HttpResponse(str(e), status_code=429)
        
            # 5. 如果有 adjustment 则把 adjustment 也保存在 同一天（客户请求的这一天）
            if not consumption_adjustment_df.empty:
                if not save_oneday_to_csv(storageaccount_name, storageaccount_key, container_name, consumption_adjustment_df, consumption_date, True):
                    return func.HttpResponse("save_oneday_to_csv_adjustement failed", status_code=500);

        # 6. 拼接 return_df  = oneday 数据 + adjustment 数据 用于最终返回
        return_df = consumption_df
        return_df = pd.concat([consumption_df, consumption_adjustment_df], axis=0, ignore_index=True)
        """
        
    # 7. 如果 page_num 为空，则仅返回行数，如果 page_num 大于超过实际，则返回行数
    total_pages = math.ceil(len(return_df) / itemcount_singlepage)

    if istest:
        try:
            current_page = int(config_data.get("test_current_page"))
        except:
            current_page =-1
    else:
        if not req.params.get('page_num', ''):
            current_page = -1
        else:
            try:
                current_page = int(req.params.get('page_num', ''))
            except ValueError:
                current_page = -1
        
    if not current_page or current_page <0:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )

    if current_page > total_pages:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "Total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )


    # 8. 分页返回 return_df 的数据
    try:
        #return_df['date'] = return_df.to_datetime(return_df['date'], format='%m/%d/%Y').dt.strftime('%Y-%m-%d')
        #return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y').return_df.strftime('%Y-%m-%d')
        return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodEndDate'] = pd.to_datetime(return_df['billingPeriodEndDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodStartDate'] = pd.to_datetime(return_df['billingPeriodStartDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df.fillna("")
    except Exception as e:
        print(e)

    all_records = []
    for i in range(0, len(return_df), itemcount_singlepage):
        paginated_df = return_df.iloc[i:i+itemcount_singlepage].copy()
        all_records.append(paginated_df)
    
    try:
        total_cost = return_df[config_data.get("itemname_totalcost")].sum()
    except Exception as e:
        raise e

    # 将分页信息添加到 Headers 中
    headers = {
        "X-Pagination-Page": str(current_page),
        "X-Pagination-Total-Pages": str(total_pages)
    }

    try:
        return_json_str = all_records[current_page-1].to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str)         
        #json_data = convert_int64_to_int(json_data)
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        headers=headers,
        status_code=200
        )
    
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": itemcount_singlepage,
        "Total_page_number" : total_pages,
        "input_page_number" : current_page,
        "total_cost": total_cost,
        "data": json_data
    }        

    #response_json = convert_int64_to_int(response_json)

    # 以 JSON 格式在 HTTP 响应中返回
    return func.HttpResponse(
        body=json.dumps(response_json, default=str),
        mimetype="application/json",
        headers=headers,
        status_code=200
    )

    
def get_month_cost(req: func.HttpRequest) -> func.HttpResponse:
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    return_df = pd.DataFrame()
    consumption_date = datetime.now()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        container_name = config_data.get("test_container_name")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    if istest: 
        try:
            consumption_date = datetime.strptime(config_data.get("test_consumption_date"), "%Y-%m")
            subscription_id = config_data.get("test_subscription_id")
            itemcount_singlepage = int(config_data.get("test_itemcount_singlepage"))
            if not itemcount_singlepage or not consumption_date or not subscription_id:
                return func.HttpResponse("bad confiugration file: config.json", status_code=500)
        except:
            return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    else:
        try:
            consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m")  
        except ValueError:
            return func.HttpResponse("Invalid date format", status_code=400)

        if not req.params.get('subscriptionid', ''):
            return func.HttpResponse("Invalid parameter: subscription id", status_code=400)
        if not is_valid_subscription_id(req.params.get('subscriptionid', '')):
            return func.HttpResponse("Invalid parameter: subscription id", status_code=400)
        subscription_id = req.params.get('subscriptionid', '')
        container_name = subscription_id

        if not req.params.get('itemcount_singlepage', ''):
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        try:
            itemcount_singlepage = int(req.params.get('itemcount_singlepage', ''))
        except ValueError:
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        
    # 0.5 检查是不是早于本月
    if consumption_date >= datetime.now().replace(day=1):
        return func.HttpResponse("Invalid date(month), later than last month", status_code=400)

    # 1. 读取指定月 所有 CSV
    return_df = load_onemonth_from_csv_with_adjustment(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        month=consumption_date)

   # 2. 检查完整性，缺少的 
    first_day_of_month = datetime(consumption_date.year, consumption_date.month, 1)
    temp_date = first_day_of_month + timedelta(days=32)
    temp_date = datetime(temp_date.year, temp_date.month, 1)
    last_day_of_month = temp_date - timedelta(days=1)
    all_days_in_month = pd.date_range(first_day_of_month, last_day_of_month)
    exist_dates = pd.to_datetime(return_df['date'], format='%m/%d/%Y')  
    if len(return_df) == 0:
        missing_dates = all_days_in_month
    else:
        exist_dates_list = list(set(exist_dates))
        missing_dates = set(all_days_in_month) - set(exist_dates_list)    

    #exist_dates_list = list(exist_dates)
    #missing_date_df = return_df[return_df['date'] == exist_dates_list[0].strftime('%m/%d/%Y')]

    # 3. 缺少的日子，调用 API， 并保存 CSV，并把数据集补充入 returndf
    if len(missing_dates) > 0:
        api_df = load_onemonth_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                subscription_id=subscription_id, 
                base_url=base_url, 
                consumption_date=consumption_date)
        for missing_date in missing_dates:
            missing_date_df = api_df[api_df['date'] == missing_date.strftime('%m/%d/%Y')]
            if not save_oneday_to_csv(
                storageaccount_name=storageaccount_name, 
                storageaccount_key=storageaccount_key, 
                container_name=container_name, 
                consumption_dataframe=missing_date_df, 
                consumption_date=missing_date,
                isadjustment=False):
                return func.HttpResponse(f"save consumption to CSV failed: date = {missing_date}", status_code=500)
            else:
                return_df = pd.concat([return_df, missing_date_df], ignore_index=True)

    # 4. 如果 page_num 为空，则仅返回行数，如果 page_num 大于超过实际，则返回行数
    total_pages = math.ceil(len(return_df) / itemcount_singlepage)

    if istest:
        try:
            current_page = int(config_data.get("test_current_page"))
        except:
            current_page =-1
    else:
        if not req.params.get('page_num', ''):
            current_page = -1
        else:
            try:
                current_page = int(req.params.get('page_num', ''))
            except ValueError:
                current_page = -1
        
    if not current_page or current_page <0:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )

    if current_page > total_pages:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "Total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )


    # 5. 分页返回 return_df 的数据
    try:
        #return_df['date'] = return_df.to_datetime(return_df['date'], format='%m/%d/%Y').dt.strftime('%Y-%m-%d')
        #return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y').return_df.strftime('%Y-%m-%d')
        return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodEndDate'] = pd.to_datetime(return_df['billingPeriodEndDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodStartDate'] = pd.to_datetime(return_df['billingPeriodStartDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df.fillna("")
    except Exception as e:
        print(e)

    all_records = []
    for i in range(0, len(return_df), itemcount_singlepage):
        paginated_df = return_df.iloc[i:i+itemcount_singlepage].copy()
        all_records.append(paginated_df)
    
    try:
        total_cost = return_df[config_data.get("itemname_totalcost")].sum()
    except Exception as e:
        raise e

    # 将分页信息添加到 Headers 中
    headers = {
        "X-Pagination-Page": str(current_page),
        "X-Pagination-Total-Pages": str(total_pages)
    }

    try:
        return_json_str = all_records[current_page-1].to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str)                     
        #json_data = convert_int64_to_int(json_data)
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        headers=headers,
        status_code=200
        )
    
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": itemcount_singlepage,
        "Total_page_number" : total_pages,
        "input_page_number" : current_page,
        "total_cost": total_cost,
        "data": json_data
    }        

    # 以 JSON 格式在 HTTP 响应中返回
    return func.HttpResponse(
        body=json.dumps(response_json, default=str),
        mimetype="application/json",
        headers=headers,
        status_code=200
    )


def get_day_cost_for_EA(req: func.HttpRequest) -> func.HttpResponse:
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    return_df = pd.DataFrame()
    consumption_date = datetime.now()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        base_url = config_data.get("base_url")        
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    if istest: 
        try:
            consumption_date = datetime.strptime(config_data.get("test_consumption_date"), "%Y-%m-%d")
            itemcount_singlepage = int(config_data.get("test_itemcount_singlepage"))
            if not itemcount_singlepage or not consumption_date:
                return func.HttpResponse("bad confiugration file: config.json", status_code=500)
        except:
            return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    else:
        try:
            consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m-%d")  
        except ValueError:
            return func.HttpResponse("Invalid date format", status_code=400)

        if not req.params.get('itemcount_singlepage', ''):
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        try:
            itemcount_singlepage = int(req.params.get('itemcount_singlepage', ''))
        except ValueError:
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        
    # 1. 检查调用日的 年月日.csv 已经存在，则直接 加载 年月日.csv 及 年月日_adjustment.csv，返回
    date_str = consumption_date.strftime('%Y-%m-%d')
    month_str = consumption_date.strftime('%Y-%m')
    csv_filename = f'{month_str}/{date_str}.csv'
    adjustment_filename = f'{month_str}/{date_str}_adjustment.csv'
    if blob_file_exists(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=billingAccount_id, 
        file_name=csv_filename):
        return_df = load_oneday_from_csv_with_adjustment(
            storageaccount_name=storageaccount_name, 
            storageaccount_key=storageaccount_key, 
            container_name=billingAccount_id, 
            csv_filename=csv_filename, 
            adjustment_filename=adjustment_filename)
        return_df.fillna("")
        return_df['resourceId'] = return_df['resourceId'].fillna('RoundingAdjustment')
        #如果晚于2024年6月1日则增加6%的税
        if consumption_date >= datetime(2024,6,1):
            return_df['costInBillingCurrency'] = return_df['costInBillingCurrency'] * 1.06

    else:
        #   更新，如果不存在，直接报错
        return func.HttpResponse(f"day CSV file not exist: {consumption_date}", status_code=500)

        """
        # 2. 如果 年月日.csv 不存在，则开始编制 年月日.csv
        #    按照日期要求，获取 所有 usage details 保存在 consumption_df 只获取分摊数据 - AMORTIZED_COST_METRIC_TYPE
        try:
            consumption_df = load_oneday_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                billingAccount_id=billingAccount_id,
                base_url=base_url, 
                consumption_date=consumption_date)
            consumption_df.fillna("")
        except CustomException429 as e:
            return func.HttpResponse(str(e), status_code=429)

        # 3. 把这一天的数据保存在 CSV （客户请求的这一天）
        if not save_oneday_to_csv(
            storageaccount_name=storageaccount_name, 
            storageaccount_key=storageaccount_key, 
            container_name=billingAccount_id, 
            consumption_dataframe=consumption_df, 
            consumption_date=consumption_date, 
            isadjustment=False):
            return func.HttpResponse("save_oneday_to_csv failed", status_code=500);

        # 4. 开始计算上个月的 adjustment； 
        #    重新从 ARM_consumption_api 加载上月的数据 至 lastmonth_consumption_df
        #    加载 本月所有 adjustment.csv 内容至 lastmonth_adjustment_df
        #    逐个资源，使用 lastmonth_consumption_df 中的总价，减去 saved_df 中的总价  再减去 lastmonth_adjustment_df 中的总价，获得该资源今日 adjustment 数据，保存至 年月日_adjustment_df；
        #    如果有 年月日_adjustment_df 不为空，则保存在 storage account\年月日adjustment.csv；如果文件已经存在，则覆盖

        #    判断是否需要计算 adjustment
        if (consumption_date.year == datetime.now().year and 
            consumption_date.month == datetime.now().month and 
            consumption_date.day < 8 ):
            # 如果输入日期是本月的1-7日，则计算 adjustment
            try:
                consumption_adjustment_df = calculate_adjustment(
                    client_id=client_id, 
                    client_secret=client_secret,
                    tenant_id=tenant_id,                    
                    base_url=base_url,
                    billingAccount_id=billingAccount_id,
                    storageaccount_name=storageaccount_name,
                    storageaccount_key=storageaccount_key,
                    container_name=billingAccount_id,
                    save_date=consumption_date)
                consumption_adjustment_df.fillna("")
            except CustomException429 as e:
                return func.HttpResponse(str(e), status_code=429)
        
            # 5. 如果有 adjustment 则把 adjustment 也保存在 同一天（客户请求的这一天）
            if not consumption_adjustment_df.empty:
                if not save_oneday_to_csv(
                    storageaccount_name=storageaccount_name, 
                    storageaccount_key=storageaccount_key, 
                    container_name=billingAccount_id, 
                    consumption_dataframe=consumption_adjustment_df, 
                    consumption_date=consumption_date, 
                    isadjustment=True):
                    return func.HttpResponse("save_oneday_to_csv_adjustement failed", status_code=500);

        # 6. 拼接 return_df  = oneday 数据 + adjustment 数据 用于最终返回
        return_df = consumption_df
        return_df = pd.concat([consumption_df, consumption_adjustment_df], axis=0, ignore_index=True)
        """
        
    # 7. 如果 page_num 为空，则仅返回行数，如果 page_num 大于超过实际，则返回行数
    total_pages = math.ceil(len(return_df) / itemcount_singlepage)

    if istest:
        try:
            current_page = int(config_data.get("test_current_page"))
        except:
            current_page =-1
    else:
        if not req.params.get('page_num', ''):
            current_page = -1
        else:
            try:
                current_page = int(req.params.get('page_num', ''))
            except ValueError:
                current_page = -1
        
    if not current_page or current_page <0:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )

    if current_page > total_pages:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "Total_page_number" : total_pages,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )


    # 8. 分页返回 return_df 的数据
    try:
        #return_df['date'] = return_df.to_datetime(return_df['date'], format='%m/%d/%Y').dt.strftime('%Y-%m-%d')
        #return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y').return_df.strftime('%Y-%m-%d')
        return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodEndDate'] = pd.to_datetime(return_df['billingPeriodEndDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodStartDate'] = pd.to_datetime(return_df['billingPeriodStartDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df.fillna("")
    except Exception as e:
        print(e)

    all_records = []
    for i in range(0, len(return_df), itemcount_singlepage):
        paginated_df = return_df.iloc[i:i+itemcount_singlepage].copy()
        all_records.append(paginated_df)
    
    try:
        total_cost = return_df[config_data.get("itemname_totalcost")].sum()
    except Exception as e:
        raise e

    # 将分页信息添加到 Headers 中
    headers = {
        "X-Pagination-Page": str(current_page),
        "X-Pagination-Total-Pages": str(total_pages)
    }

    try:
        return_json_str = all_records[current_page-1].to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str)                     
        #json_data = convert_int64_to_int(json_data)
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        headers=headers,
        status_code=200
        )
    
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": itemcount_singlepage,
        "Total_page_number" : total_pages,
        "input_page_number" : current_page,
        "total_cost": total_cost,
        "data": json_data
    }        

    #response_json = convert_int64_to_int(response_json)

    #加trace，应对志强的 数字不一样的问题
    logging.info(f'tracing daily billing data, total cost: {total_cost}, requested date: {consumption_date}, return EA day cost at {datetime.now()}')

    # 以 JSON 格式在 HTTP 响应中返回
    return func.HttpResponse(
        body=json.dumps(response_json, default=str),
        mimetype="application/json",
        headers=headers,
        status_code=200
    )


def get_month_cost_for_EA(req: func.HttpRequest) -> func.HttpResponse:
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    return_df = pd.DataFrame()
    consumption_date = datetime.now()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        container_name = config_data.get("test_container_name")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    if istest: 
        try:
            consumption_date = datetime.strptime(config_data.get("test_consumption_date"), "%Y-%m")
            subscription_id = config_data.get("test_subscription_id")
            itemcount_singlepage = int(config_data.get("test_itemcount_singlepage"))
            if not itemcount_singlepage or not consumption_date or not subscription_id:
                return func.HttpResponse("bad confiugration file: config.json", status_code=500)
        except:
            return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    else:
        try:
            consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m")  
        except ValueError:
            return func.HttpResponse("Invalid date format", status_code=400)

        billingAccount_id = req.params.get('billingAccountid', '')
        container_name = billingAccount_id

        if not req.params.get('itemcount_singlepage', ''):
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        try:
            itemcount_singlepage = int(req.params.get('itemcount_singlepage', ''))
        except ValueError:
            return func.HttpResponse("Invalid parameter: itemcount_singlepage", status_code=400)
        
    # 0.5 检查是不是早于本月
    if consumption_date >= datetime.now().replace(day=1):
        return func.HttpResponse("Invalid date(month), later than last month", status_code=400)

    # 1. 读取指定月 所有 CSV
    return_df = load_onemonth_from_csv_with_adjustment(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        month=consumption_date)


    return_df['resourceId'] = return_df['resourceId'].fillna('RoundingAdjustment')

    # 2. 检查完整性，缺少的 - 不再需要了
    """
    first_day_of_month = datetime(consumption_date.year, consumption_date.month, 1)
    temp_date = first_day_of_month + timedelta(days=32)
    temp_date = datetime(temp_date.year, temp_date.month, 1)
    last_day_of_month = temp_date - timedelta(days=1)
    all_days_in_month = pd.date_range(first_day_of_month, last_day_of_month)
    exist_dates = pd.to_datetime(return_df['date'], format='%m/%d/%Y')  
    if len(return_df) == 0:
        missing_dates = all_days_in_month
    else:
        exist_dates_list = list(set(exist_dates))
        missing_dates = set(all_days_in_month) - set(exist_dates_list)    

    #exist_dates_list = list(exist_dates)
    #missing_date_df = return_df[return_df['date'] == exist_dates_list[0].strftime('%m/%d/%Y')]

    # 3. 缺少的日子，调用 API， 并保存 CSV，并把数据集补充入 returndf
    if len(missing_dates) > 0:
        api_df = load_onemonth_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                billingAccount_id=billingAccount_id, 
                base_url=base_url, 
                consumption_date=consumption_date)
        for missing_date in missing_dates:
            missing_date_df = api_df[api_df['date'] == missing_date.strftime('%m/%d/%Y')]
            if not save_oneday_to_csv(
                storageaccount_name=storageaccount_name, 
                storageaccount_key=storageaccount_key, 
                container_name=container_name, 
                consumption_dataframe=missing_date_df, 
                consumption_date=missing_date,
                isadjustment=False):
                return func.HttpResponse(f"save consumption to CSV failed: date = {missing_date}", status_code=500)
            else:
                return_df = pd.concat([return_df, missing_date_df], ignore_index=True)
        """
       
    total_cost = return_df[config_data.get("itemname_totalcost")].sum()

    return_df['resourceId'].fillna('RoundingAdjustment', inplace=True)
    # 3.5 return_df 中消除计算 sum 值
    cols = return_df.columns.tolist()  
    # 创建一个字典，其中每个列的默认函数是 'first'  
    agg_dict = {col: 'first' for col in cols}  
    # 对于需要求和的列，将函数改为 'sum'  
    agg_dict.update({'costInBillingCurrency': 'sum'})  
    # 现在，我们可以使用这个字典进行 groupby 操作  
    return_df = return_df.groupby('resourceId', as_index=False).agg(agg_dict)  

    # 4. 如果 page_num 为空，则仅返回行数，如果 page_num 大于超过实际，则返回行数
    total_pages = math.ceil(len(return_df) / itemcount_singlepage)

    if istest:
        try:
            current_page = int(config_data.get("test_current_page"))
        except:
            current_page =-1
    else:
        if not req.params.get('page_num', ''):
            current_page = -1
        else:
            try:
                current_page = int(req.params.get('page_num', ''))
            except ValueError:
                current_page = -1
        
    if not current_page or current_page <0:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "total_page_number" : total_pages,
            "total_cost": total_cost,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )

    if current_page > total_pages:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "Total_page_number" : total_pages,
            "total_cost": total_cost,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )


    try:
        return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodEndDate'] = pd.to_datetime(return_df['billingPeriodEndDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodStartDate'] = pd.to_datetime(return_df['billingPeriodStartDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df.fillna("")
    except Exception as e:
        print(e)

    # 5. 分页返回 return_df 的数据
    all_records = []
    for i in range(0, len(return_df), itemcount_singlepage):
        paginated_df = return_df.iloc[i:i+itemcount_singlepage].copy()
        all_records.append(paginated_df)
    
    try:
        total_cost = return_df[config_data.get("itemname_totalcost")].sum()
    except Exception as e:
        raise e

    # 将分页信息添加到 Headers 中
    headers = {
        "X-Pagination-Page": str(current_page),
        "X-Pagination-Total-Pages": str(total_pages)
    }

    try:
        return_json_str = all_records[current_page-1].to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str)                     
        #json_data = convert_int64_to_int(json_data)
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        headers=headers,
        status_code=200
        )
    
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": itemcount_singlepage,
        "Total_page_number" : total_pages,
        "input_page_number" : current_page,
        "total_cost": total_cost,
        "data": json_data
    }        

    #response_json = convert_int64_to_int(response_json)

    # 以 JSON 格式在 HTTP 响应中返回
    return func.HttpResponse(
        body=json.dumps(response_json, default=str),
        mimetype="application/json",
        headers=headers,
        status_code=200
    )


def get_month_msft_bill_for_EA(req: func.HttpRequest) -> func.HttpResponse:
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    return_df = pd.DataFrame()
    consumption_date = datetime.now()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        base_url = config_data.get("base_url")        
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    if istest: 
        try:
            consumption_date = datetime.strptime(config_data.get("test_consumption_date"), "%Y-%m")
            itemcount_singlepage = int(config_data.get("test_itemcount_singlepage"))
        except:
            return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    else:
        try:
            consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m") 
            itemcount_singlepage = int(req.params.get('itemcount_singlepage', '')) 
        except ValueError:
            return func.HttpResponse("Invalid date format", status_code=400)
        
    # 1. 检查调用日的 年月日.csv 已经存在，则直接 加载 年月日.csv 及 年月日_adjustment.csv，返回
    month_str = consumption_date.strftime('%Y-%m')
    csv_filename = f'{month_str}/{month_str}.csv'
    if blob_file_exists(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=billingAccount_id, 
        file_name=csv_filename):
        return_df = load_onemonth_msft_bill_from_csv(
            storageaccount_name=storageaccount_name, 
            storageaccount_key=storageaccount_key, 
            container_name=billingAccount_id, 
            csv_filename=csv_filename)
        return_df['resourceId'] = return_df['resourceId'].fillna('RoundingAdjustment')
        if consumption_date >= datetime(2024,5,1):
            return_df['costInBillingCurrency'] = return_df['costInBillingCurrency'] * 1.06
    else:
        #   更新，如果不存在，直接报错
        return func.HttpResponse(f"day CSV file not exist, might because monthly bill haven't been generated: {consumption_date}", status_code=500)

    # 3.5 return_df 中消除计算 sum 值
    cols = return_df.columns.tolist()  
    # 创建一个字典，其中每个列的默认函数是 'first'  
    agg_dict = {col: 'first' for col in cols}  
    # 对于需要求和的列，将函数改为 'sum'  
    agg_dict.update({'costInBillingCurrency': 'sum'})  
    # 现在，我们可以使用这个字典进行 groupby 操作  
    return_df = return_df.groupby('resourceId', as_index=False).agg(agg_dict)
    total_cost = return_df[config_data.get("itemname_totalcost")].sum()

# 7. 如果 page_num 为空，则仅返回行数，如果 page_num 大于超过实际，则返回行数
    total_pages = math.ceil(len(return_df) / itemcount_singlepage)

    if istest:
        try:
            current_page = int(config_data.get("test_current_page"))
        except:
            current_page =-1
    else:
        if not req.params.get('page_num', ''):
            current_page = -1
        else:
            try:
                current_page = int(req.params.get('page_num', ''))
            except ValueError:
                current_page = -1
        
    if not current_page or current_page <0:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "total_page_number" : total_pages,
            "total_cost": total_cost,            
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )

    if current_page > total_pages:
        response_json = {
            "cost_item_number": len(return_df),
            "item_number_in_singl_page": itemcount_singlepage,
            "Total_page_number" : total_pages,
            "total_cost": total_cost,
            "input_page_number" : current_page
        }        
        return func.HttpResponse(
            body=json.dumps(response_json),
            mimetype="application/json",
            headers=response_json,
            status_code=200
        )


    # 8. 分页返回 return_df 的数据
    try:
        return_df['date'] = pd.to_datetime(return_df['date'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodEndDate'] = pd.to_datetime(return_df['billingPeriodEndDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df['billingPeriodStartDate'] = pd.to_datetime(return_df['billingPeriodStartDate'], format='%m/%d/%Y') \
                   .apply(lambda x: x.strftime('%Y-%m-%d'))
        return_df.fillna("")
    except Exception as e:
        print(e)

    all_records = []
    for i in range(0, len(return_df), itemcount_singlepage):
        paginated_df = return_df.iloc[i:i+itemcount_singlepage].copy()
        all_records.append(paginated_df)
    

    # 将分页信息添加到 Headers 中
    headers = {
        "X-Pagination-Page": str(current_page),
        "X-Pagination-Total-Pages": str(total_pages)
    }

    try:
        return_json_str = all_records[current_page-1].to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str)        
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        headers=headers,
        status_code=200
        )
    
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": itemcount_singlepage,
        "Total_page_number" : total_pages,
        "input_page_number" : current_page,
        "total_cost": total_cost,
        "data": json_data
    }        

    #加trace，应对志强的 数字不一样的问题
    logging.info(f'tracing monthly msft billing data, total cost: {total_cost}, requested date: {consumption_date}, return EA day cost at {datetime.now()}')

    # 以 JSON 格式在 HTTP 响应中返回
    return func.HttpResponse(
        body=json.dumps(response_json, default=str),
        mimetype="application/json",
        headers=headers,
        status_code=200
    )
'''
    response_json = {
        "cost_item_number": len(return_df),
        "total_cost": total_cost,
        "total_page_number": 1
       }

    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": len(return_df),
        "Total_page_number" : 1,
        "input_page_number" : 1,
        "total_cost": total_cost,
        }

    try:
        return_json_str = return_df.to_json(orient='records', default_handler=str)
        json_data = json.loads(return_json_str) 
    except Exception as e:
        return func.HttpResponse(
        body=e.msg,
        status_code=200
        )
        
    response_json = {
        "cost_item_number": len(return_df),
        "item_number_in_singl_page": len(return_df),
        "Total_page_number" : 1,
        "input_page_number" : 1,
        "total_cost": total_cost,
        "data": json_data
    }

    return func.HttpResponse(
        body=json.dumps(response_json),
        mimetype="application/json",
        status_code=200
    )
   '''
 

@app.route(route="init_month", auth_level=func.AuthLevel.ANONYMOUS, methods=['GET'])
def init_month(req: func.HttpRequest) -> func.HttpResponse:
    logging.info(f'start init_month at{datetime.now()}')

    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    if (not istest) and (req.headers.get("Authorization") != config_data.get("prod_token")):
        token = req.headers.get("Authorization")
        return func.HttpResponse("show me the secret", status_code=401)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    container_name = billingAccount_id
    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    try:
        cost_month = datetime.strptime(req.params.get('month', ''), "%Y-%m")  
    except ValueError:
        return func.HttpResponse("Invalid date format", status_code=400)

    # 0.5 检查是不是早于本月
    if cost_month > datetime.now().replace(day=1):
        return func.HttpResponse("Invalid date(month), later than last month", status_code=400)

    # 1. 读取指定月 所有 CSV
    return_df = load_onemonth_from_csv_without_adjustment(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        month=cost_month)

    # 2. 检查完整性，缺少的 
    first_day_of_month = datetime(cost_month.year, cost_month.month, 1)
    temp_date = first_day_of_month + timedelta(days=32)
    temp_date = datetime(temp_date.year, temp_date.month, 1)
    last_day_of_month = temp_date - timedelta(days=1)
    all_days_in_month = pd.date_range(first_day_of_month, last_day_of_month)
    if len(return_df) == 0:
        missing_dates = all_days_in_month
    else:
        exist_dates = pd.to_datetime(return_df['date'], format='%m/%d/%Y')  
        exist_dates_list = list(set(exist_dates))
        missing_dates = set(all_days_in_month) - set(exist_dates_list)    

    # 3. 缺少的日子，调用 API， 并保存 CSV，并把数据集补充入 returndf
    return_str = " \n "
    if len(missing_dates) > 0:
        api_df = load_onemonth_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                billingAccount_id=billingAccount_id, 
                base_url=base_url, 
                consumption_date=cost_month)
        for missing_date in missing_dates:
            missing_date_df = api_df[api_df['date'] == missing_date.strftime('%m/%d/%Y')]
            return_str = return_str + missing_date.strftime('%Y-%m-%d') + " \n "
            if len(missing_date_df) != 0: 
                if not save_oneday_to_csv(
                    storageaccount_name=storageaccount_name, 
                    storageaccount_key=storageaccount_key, 
                    container_name=container_name, 
                    consumption_dataframe=missing_date_df, 
                    consumption_date=missing_date,
                    isadjustment=False):
                    return func.HttpResponse(f"save consumption to CSV failed: date = {return_str}", status_code=500)
                
    logging.info(f'complete init_month, added dates: {return_str}. at {datetime.now()}')
    return func.HttpResponse(f"following dates being added: {return_str}", status_code=200)


@app.route(route="init_day", auth_level=func.AuthLevel.ANONYMOUS, methods=['GET'])
def init_day(req: func.HttpRequest) -> func.HttpResponse:
    logging.info(f'start init_day at{datetime.now()}')

    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]
 
    # 0. 检查输入参数，如果格式错误，或者没有值，返回
    #    
    #   date 获取的账单日
    #   page 页号
    #   subscriptionid 订阅ID
    consumption_df = pd.DataFrame()
    consumption_adjustment_df = pd.DataFrame()

    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        container_name = config_data.get("test_container_name")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")

    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    container_name = billingAccount_id
    consumption_date = datetime.strptime(req.params.get('date', ''), "%Y-%m-%d")  
        
    # 1. 检查调用日的 年月日.csv 已经存在，则直接 加载 年月日.csv 及 年月日_adjustment.csv，返回
    date_str = consumption_date.strftime('%Y-%m-%d')
    month_str = consumption_date.strftime('%Y-%m')
    csv_filename = f'{month_str}/{date_str}.csv'

    if blob_file_exists(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        file_name=csv_filename):
        return func.HttpResponse(f"day data existed: {consumption_date}", status_code=200)
    else:
        # 2. 如果 年月日.csv 不存在，则开始编制 年月日.csv
        #    按照日期要求，获取 所有 usage details 保存在 consumption_df 只获取分摊数据 - AMORTIZED_COST_METRIC_TYPE
        try:
            consumption_df = load_oneday_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                billingAccount_id=billingAccount_id, 
                base_url=base_url, 
                consumption_date=consumption_date)
            consumption_df.fillna("")
        except CustomException429 as e:
            return func.HttpResponse(str(e), status_code=429)

        # 3. 把这一天的数据保存在 CSV （客户请求的这一天）
        if not save_oneday_to_csv(storageaccount_name, storageaccount_key, container_name, consumption_df, consumption_date, False):
            return func.HttpResponse("save_oneday_to_csv failed", status_code=500);

        # 4. 开始计算上个月的 adjustment； 
        #    重新从 ARM_consumption_api 加载上月的数据 至 lastmonth_consumption_df
        #    加载 本月所有 adjustment.csv 内容至 lastmonth_adjustment_df
        #    逐个资源，使用 lastmonth_consumption_df 中的总价，减去 saved_df 中的总价  再减去 lastmonth_adjustment_df 中的总价，获得该资源今日 adjustment 数据，保存至 年月日_adjustment_df；
        #    如果有 年月日_adjustment_df 不为空，则保存在 storage account\年月日adjustment.csv；如果文件已经存在，则覆盖

        #    判断是否需要计算 adjustment
        adjustment_day = config_data.get("adjustment_day")
        if (consumption_date.year == datetime.now().year and 
            consumption_date.month == datetime.now().month and 
            consumption_date.day == adjustment_day ):
            # 如果输入日期是本月的1-7日，则计算 adjustment
            try:
                consumption_adjustment_df = calculate_adjustment(
                    client_id=client_id, 
                    client_secret=client_secret,
                    tenant_id=tenant_id,
                    billingAccount_id=billingAccount_id,                    
                    base_url=base_url,
                    storageaccount_name=storageaccount_name,
                    storageaccount_key=storageaccount_key,
                    container_name=container_name,
                    save_date=consumption_date)
                consumption_adjustment_df.fillna("")
            except CustomException429 as e:
                return func.HttpResponse(str(e), status_code=429)
        
            # 5. 如果有 adjustment 则把 adjustment 也保存在 同一天（客户请求的这一天）
            if not consumption_adjustment_df.empty:
                if not save_oneday_to_csv(storageaccount_name, 
                                          storageaccount_key, 
                                          container_name, 
                                          consumption_adjustment_df, 
                                          consumption_date, 
                                          True):
                    return func.HttpResponse("save_oneday_to_csv_adjustement failed", status_code=500);
                else:
                    logging.info(f'complete init_day, day data and adjustment data saved: {consumption_date}. at {datetime.now()}')
                    return func.HttpResponse(f"day data and adjustment data saved: {consumption_date}", status_code=200)
        else:
            logging.info(f'complete init_day, day data saved: {consumption_date}. at {datetime.now()}')
            return func.HttpResponse(f"day data saved: {consumption_date}", status_code=200)


with open('config.json') as config_file:
    config_data = json.load(config_file)  
daily_init_time = config_data["daily_init_time"]
@app.timer_trigger(schedule=f"{daily_init_time} * * *", arg_name="myTimer", run_on_startup=False,
              use_monitor=False) 
def timer_init_day(myTimer: func.TimerRequest) -> None:
    
    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    if istest:
        init_day_url = config_data.get("test_init_day_url")       
        init_month_msft_bill_url = config_data.get("test_init_month_msft_bill_url") 
    else:
        init_day_url = config_data.get("prod_init_day_url")
        init_month_msft_bill_url = config_data.get("prod_init_month_msft_bill_url")

    headers = {
        'Authorization': '79931E79-1F7C-63E8-725F-171D236C5236',  # 替换为实际的访问令牌
        'Content-Type': 'application/json',  # 可以替换为你的应用名称
    }

    # 设置请求的 URL    
    #url = 'http://localhost:7071/api/init_day?date=2023-12&billingAccountid=59285102&itemcount_singlepage=16000&page_num=1'
    today = datetime.now()
    
    two_days_ago = today - timedelta(2)
    url = init_day_url + f"&date={two_days_ago.year}-{two_days_ago.month}-{two_days_ago.day}"

    logging.info(f'invoke init_day url: {url}. at {datetime.now()}')

    # 发起 GET 请求
    response = requests.get(url, headers=headers)
    
    # 打印 HTTP 响应
    print(f"HTTP 状态码: {response.status_code}")
    print("响应头部:")
    print(response.headers)
    print("响应内容:")
    print(response.text)

    if today.day == config_data.get("monthlybilling_day"):
        response = requests.get(init_month_msft_bill_url, headers=headers)
        # 打印 HTTP 响应
        print(f"HTTP 状态码: {response.status_code}")
        print("响应头部:")
        print(response.headers)
        print("响应内容:")
        print(response.text)

@app.route(route="Init_month_msft_bill", auth_level=func.AuthLevel.ANONYMOUS, methods=['GET'])
def Init_month_msft_bill(req: func.HttpRequest) -> func.HttpResponse:
    logging.info(f'start init_monthly_msft_bill at{datetime.now()}')

    with open('config.json') as config_file:
        config_data = json.load(config_file)  
    istest = config_data["istest"]

    if (not istest) and (req.headers.get("Authorization") != config_data.get("prod_token")):
        token = req.headers.get("Authorization")
        return func.HttpResponse("show me the secret", status_code=401)
    
    billingAccount_id = req.params.get('billingAccountid', '')
    container_name = billingAccount_id
    if istest:
        client_id = config_data.get("test_client_id")
        client_secret = config_data.get("test_client_secret")
        tenant_id = config_data.get("test_tenant_id")        
        storageaccount_name = config_data.get("test_storageaccount_name")
        storageaccount_key = config_data.get("test_storageaccount_key")
        base_url = config_data.get("base_url")
    else:
        client_id = config_data.get("prod_client_id")
        client_secret = config_data.get("prod_client_secret")
        tenant_id = config_data.get("prod_tenant_id")        
        storageaccount_name = config_data.get("prod_storageaccount_name")
        storageaccount_key = config_data.get("prod_storageaccount_key")
        base_url = config_data.get("base_url")
    if not client_id or not client_secret or not tenant_id or not storageaccount_key or not storageaccount_name or not base_url:
        return func.HttpResponse("bad confiugration file: config.json", status_code=500)
    
    last_month_number = datetime.now().month - 1
    year_number = datetime.now().year 
    if datetime.now().month == 1:
        last_month_number = 12
        year_number = year_number -1
    #取上个月1日为 consumption date
    consumption_date = datetime(year=year_number, month=last_month_number, day=1)
        
    month_str = consumption_date.strftime('%Y-%m')
    csv_filename = f'{month_str}/{month_str}.csv'

    if blob_file_exists(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        file_name=csv_filename):
        return func.HttpResponse(f"day data existed: {consumption_date}", status_code=200)
    else:
        try:
            consumption_df = load_onemonth_from_api(
                client_id=client_id, 
                client_secret=client_secret, 
                tenant_id=tenant_id, 
                billingAccount_id=billingAccount_id, 
                base_url=base_url, 
                consumption_date=consumption_date)
            consumption_df.fillna("")
        except CustomException429 as e:
            return func.HttpResponse(str(e), status_code=429)

        # 3. 把这一天的数据保存在 CSV （客户请求的这一天）
        if not save_onemonth_msft_bill_to_csv(storageaccount_name, storageaccount_key, container_name, consumption_df, consumption_date):
            return func.HttpResponse("save_onemonth_msft_bill_to_csv failed", status_code=500);
       
        logging.info(f'complete init_month_msft_bill, month data saved: {consumption_date}. at {datetime.now()}')
        return func.HttpResponse(f"month_msft_bill data saved: {consumption_date}", status_code=200)