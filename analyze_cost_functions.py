import os
import io
import re
import time
import json
import asyncio
import threading
import concurrent.futures
from typing import List
from datetime import datetime, timedelta
from azure.identity import ClientSecretCredential  
from azure.mgmt.consumption import ConsumptionManagementClient
import pandas as pd
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from io import StringIO
from azure.identity import ClientSecretCredential   
from azure.mgmt.consumption import ConsumptionManagementClient
from azure.core.polling import LROPoller  
from azure.core.polling.base_polling import OperationResourcePolling  
from azure.core.pipeline import Pipeline  
from azure.core.pipeline.policies import HTTPPolicy  
from azure.core.pipeline.transport import HttpRequest  
from datetime import datetime, timedelta
from azure.storage.blob import BlobServiceClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import CostDetailsOperationResults
from azure.mgmt.costmanagement.models import GenerateCostDetailsReportRequestDefinition
from azure.mgmt.costmanagement.models import CostDetailsMetricType
from azure.mgmt.costmanagement.models import CostDetailsTimePeriod
from azure.mgmt.costmanagement.operations import GenerateCostDetailsReportOperations
from azure.identity import ClientSecretCredential 
from queue import Queue


def save_oneday_to_csv(storageaccount_name:str, storageaccount_key:str, container_name:str, consumption_dataframe:pd.DataFrame, consumption_date:datetime, isadjustment:bool) -> bool:
    blob_service_client = BlobServiceClient(account_url=f'https://{storageaccount_name}.blob.core.windows.net', credential=storageaccount_key)
    #blob_service_client = return_blob_with_SPN_Auth(storageaccount_name=storageaccount_name, storageaccount_key=storageaccount_key)
    container_client = blob_service_client.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()

    date_str = consumption_date.strftime('%Y-%m-%d')
    month_str = consumption_date.strftime('%Y-%m')
    csv_fullname = month_str
    if(isadjustment):
        csv_fullname = csv_fullname + f'/{date_str}_adjustment.csv'
    else:
        csv_fullname = csv_fullname + f'/{date_str}.csv'
    
    # 保存 DataFrame 到 CSV 文件
    csv_data = consumption_dataframe.to_csv(index=False, encoding='utf-8-sig')

    file_exists = blob_file_exists(storageaccount_name, storageaccount_key, container_name, csv_fullname)
    if file_exists:
        return False
        
    try:
        blob_client = container_client.get_blob_client(csv_fullname)
        blob_client.upload_blob(csv_data, encoding='utf-8-sig')
        print(f'CSV file {csv_fullname} uploaded to Azure Storage.')
    except Exception as e:
        raise e
    return True


def save_onemonth_msft_bill_to_csv(storageaccount_name:str, storageaccount_key:str, container_name:str, consumption_dataframe:pd.DataFrame, consumption_date:datetime) -> bool:
    blob_service_client = BlobServiceClient(account_url=f'https://{storageaccount_name}.blob.core.windows.net', credential=storageaccount_key)
    container_client = blob_service_client.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()

    month_str = consumption_date.strftime('%Y-%m')
    csv_fullname = month_str + f'/{month_str}.csv'
    
    # 保存 DataFrame 到 CSV 文件
    csv_data = consumption_dataframe.to_csv(index=False, encoding='utf-8-sig')

    file_exists = blob_file_exists(storageaccount_name, storageaccount_key, container_name, csv_fullname)
    if file_exists:
        return False
        
    try:
        blob_client = container_client.get_blob_client(csv_fullname)
        blob_client.upload_blob(csv_data, encoding='utf-8-sig')
        print(f'CSV file {csv_fullname} uploaded to Azure Storage.')
    except Exception as e:
        raise e
    return True


def load_onemonth_msft_bill_from_csv(storageaccount_name:str, storageaccount_key:str, container_name:str, csv_filename: str) -> pd.DataFrame:
    return_df = pd.DataFrame()
    # 创建 BlobServiceClient
    blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)

    # 获取 Blob 容器和 Blob 客户端
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(csv_filename)

    # 下载 Blob 中的数据
    try:
        blob_data = blob_client.download_blob()
        csv_data = blob_data.readall().decode('utf-8-sig')
    except Exception as e:
        if e.error_code == "BlobNotFound":
            return return_df
        else:
            raise e

    # 将 CSV 数据加载到 pandas DataFrame
    return_df = pd.read_csv(StringIO(csv_data), encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
    return return_df

    
def blob_file_exists(storageaccount_name: str, storageaccount_key: str, container_name: str, file_name: str) -> bool:
    
    blob_service_client = BlobServiceClient(account_url=f'https://{storageaccount_name}.blob.core.windows.net', credential=storageaccount_key)
    #blob_service_client = return_blob_with_SPN_Auth(storageaccount_name=storageaccount_name, storageaccount_key=storageaccount_key)
    container_client = blob_service_client.get_container_client(container_name)
    if not container_client.exists():
        container_client.create_container()

    try:
        blob_client = container_client.get_blob_client(file_name)
        blob_client.get_blob_properties()
        return True
    except Exception as e:
        #if e
        if e.error_code == "BlobNotFound":
            return False
        else:
            raise e


def load_oneday_from_csv_with_adjustment(storageaccount_name:str, storageaccount_key:str, container_name:str, csv_filename: str, adjustment_filename: str) -> pd.DataFrame:
    return_df = pd.DataFrame()
    consumption_df = pd.DataFrame()
    adjustment_df = pd.DataFrame()

    # 创建 BlobServiceClient
    blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)

    # 获取 Blob 容器和 Blob 客户端
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(csv_filename)

    # 下载 Blob 中的数据
    try:
        blob_data = blob_client.download_blob()
        csv_data = blob_data.readall().decode('utf-8-sig')
    except Exception as e:
        if e.error_code == "BlobNotFound":
            return return_df
        else:
            raise e

    # 将 CSV 数据加载到 pandas DataFrame
    consumption_df = pd.read_csv(StringIO(csv_data), encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
    return_df = consumption_df

    if adjustment_filename and blob_file_exists(storageaccount_name=storageaccount_name, storageaccount_key=storageaccount_key, container_name=container_name, file_name=adjustment_filename):
        # 创建 BlobServiceClient
        blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)

        # 获取 Blob 容器和 Blob 客户端
        blob_client = container_client.get_blob_client(adjustment_filename)

        # 下载 Blob 中的数据
        try:
            blob_data = blob_client.download_blob()
            csv_data = blob_data.readall().decode('utf-8-sig')
        except Exception as e:
            if e.error_code == "BlobNotFound":
                return return_df
            else:
                raise e

        # 将 CSV 数据加载到 pandas DataFrame
        adjustment_df = pd.read_csv(StringIO(csv_data), encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
        return_df = pd.concat([consumption_df, adjustment_df], axis=0, ignore_index=True)

    return return_df


def load_oneday_from_api(client_id: str, client_secret: str, tenant_id: str, billingAccount_id:str, base_url:str, consumption_date: datetime, subscription_id="") -> pd.DataFrame:
    start_datetime = consumption_date.strftime('%Y-%m-%d')
    end_datetime = consumption_date.strftime('%Y-%m-%d')
    return_df = call_consumption_api(
        client_id=client_id, 
        client_secret=client_secret,
        tenant_id=tenant_id, 
        billingAccount_id=billingAccount_id, 
        base_url=base_url, 
        start_datetime=start_datetime, 
        end_datetime=end_datetime, 
        subscription_id=subscription_id)
    return return_df
    #if len(return_df) == 0:
        #raise Exception("0 items get from consumption API, something wrong")
    #else:
    #    return return_df


def load_onemonth_from_api(client_id: str, client_secret: str, tenant_id: str, billingAccount_id:str, base_url:str, consumption_date: datetime, subscription_id="") -> pd.DataFrame:
    start_date = datetime(consumption_date.year, consumption_date.month, 1)
    end_date = start_date + timedelta(days=32)   
    end_date = datetime(end_date.year, end_date.month, 1)
    end_date = end_date - timedelta(days=1)
    return_df = call_consumption_api(
        client_id=client_id, 
        client_secret=client_secret,
        tenant_id=tenant_id, 
        billingAccount_id=billingAccount_id, 
        base_url=base_url, 
        start_datetime=start_date, 
        end_datetime=end_date, 
        subscription_id=subscription_id)
    return return_df


def load_onemonth_from_csv_without_adjustment(storageaccount_name:str, storageaccount_key:str, container_name:str, month:datetime) -> pd.DataFrame:
    return_df = pd.DataFrame()
    try:
        blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)
        container_client = blob_service_client.get_container_client(container_name)
        folder_name = month.strftime('%Y-%m')
        blobs = container_client.walk_blobs(name_starts_with=f"{folder_name}/")
        pattern = re.compile(r'^'+folder_name+r'/\d{4}-\d{2}-\d{2}\.csv$')
        for blob in blobs:
            if pattern.match(blob.name):
                blob_data = container_client.get_blob_client(blob.name).download_blob().readall()
                blob_data_io = io.BytesIO(blob_data)
                df = pd.read_csv(blob_data_io, encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
                if return_df.empty:
                    return_df = df
                else:
                    return_df = pd.concat([return_df, df], ignore_index=True)
    except Exception as e:
        raise e
    return return_df


def load_onemonth_from_csv_with_adjustment(storageaccount_name:str, storageaccount_key:str, container_name:str, month:datetime) -> pd.DataFrame:
    return_df = pd.DataFrame()
    try:
        blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)
        container_client = blob_service_client.get_container_client(container_name)
        folder_name = month.strftime('%Y-%m')
        blobs = container_client.walk_blobs(name_starts_with=f"{folder_name}/")
        #pattern = re.compile(r'^'+folder_name+r'/\d{4}-\d{2}-\d{2}\.csv$')
        for blob in blobs:
            #if pattern.match(blob.name):
            blob_data = container_client.get_blob_client(blob.name).download_blob().readall()
            blob_data_io = io.BytesIO(blob_data)
            df = pd.read_csv(blob_data_io, encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
            month_total_cost = df['costInBillingCurrency'].sum()
            if return_df.empty:
                return_df = df
            else:
                return_df = pd.concat([return_df, df], ignore_index=True)
    except Exception as e:
        raise e
    return return_df


def load_onemonth_from_csv_only_adjustment(storageaccount_name:str, storageaccount_key:str, container_name:str, month:datetime) -> pd.DataFrame:
    return_df = pd.DataFrame()
    try:
        blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)
        container_client = blob_service_client.get_container_client(container_name)
        folder_name = month.strftime('%Y-%m')
        blobs = container_client.walk_blobs(name_starts_with=f"{folder_name}/")
        pattern = re.compile(r'^' + folder_name + r'/\d{4}-\d{2}-\d{2}_adjustment\.csv$')        
        for blob in blobs:
            if pattern.match(blob.name):
                blob_data = container_client.get_blob_client(blob.name).download_blob().readall()
                blob_data_io = io.BytesIO(blob_data)
                df = pd.read_csv(blob_data_io, encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
                if return_df.empty:
                    return_df = df
                else:
                    return_df = pd.concat([return_df, df], ignore_index=True)
    except Exception as e:
        raise e
    return return_df


def save_one_month_lostdata_to_csv(storageaccount_name:str, storageaccount_key:str, container_name:str, consumption_df:pd.DataFrame, month:datetime) -> bool:
    try:
        blob_service_client = BlobServiceClient(account_url=f"https://{storageaccount_name}.blob.core.windows.net", credential=storageaccount_key)
        container_client = blob_service_client.get_container_client(container_name)
        month_str = month.strftime('%Y-%m')
        start_date = datetime(month.year, month.month, 1)
        delta = timedelta(days=32)
        end_date = start_date + delta        
        end_date = datetime(end_date.year, end_date.month, 1)
        delta = timedelta(days=1)
        end_date = end_date - delta

        #发现blob中，上个月哪些天的CSV数据 不存在
        blobs = container_client.list_blobs(name_starts_with=month_str)
        date_pattern = re.compile(r'^'+month_str+r'/\d{4}-\d{2}-\d{2}\.csv$')
        csv_filenames = []
        days_df_list = {}
        for blob in blobs:
            blob_name = blob.name
            if date_pattern.match(blob_name):
                csv_filenames.append(blob_name[-14:])

        #把账单df拆到每一天        
        api_filenames = []
        if consumption_df.empty:
            return False
        
        date_keyname = ""
        if 'Date' in consumption_df.columns:
            date_keyname = "Date"
        else:
            if 'date' in consumption_df.columns:
                date_keyname = "date"
            else:
                raise Exception("No Date/date column found in saving one month data process")
                

        for date_str, group in consumption_df.groupby(date_keyname):
            date_object = datetime.strptime(date_str, "%m/%d/%Y")
            date_str = date_object.strftime("%Y-%m-%d")
            days_df_list[date_str] = group
            api_filenames.append(f"{date_str}.csv")
        
        #对比找到不存在数据的日期
        for element in csv_filenames:
            if element in api_filenames:
                api_filenames.remove(element)
        to_save_filenames = api_filenames

        day = start_date
        while day <= end_date:
            day_str = day.strftime('%Y-%m-%d')
            file_name_with_folder = f"{month_str}/{day_str}.csv"
            file_name = f"{day_str}.csv"
            current_day_df = days_df_list[day_str]
            if file_name in to_save_filenames:
                csv_string = current_day_df.to_csv(index=False, encoding='utf-8-sig')
                file_exists = blob_file_exists(storageaccount_name, storageaccount_key, container_name, file_name_with_folder)
                if file_exists:
                    return False
                blob_client = container_client.get_blob_client(file_name_with_folder)
                blob_client.upload_blob(csv_string, encoding='utf-8-sig')
                day += timedelta(days=1)
            else:
                day += timedelta(days=1)
    except Exception as e:
        print(f'Error creating container: {e}')
        raise e  
    
    return True

#计算 调整数值，用于只计算当前日期的上一个月的调整数字
def calculate_adjustment(client_id: str, client_secret: str, tenant_id: str, billingAccount_id:str, base_url:str, storageaccount_name:str, storageaccount_key:str, container_name:str, save_date:datetime, subscription_id="") -> pd.DataFrame:
    # 0. 声明变量
    api_df = pd.DataFrame()
    api_after_group_df = pd.DataFrame()
    csv_df = pd.DataFrame()
    csv_after_group_df = pd.DataFrame()
    adjustment_df = pd.DataFrame()

    first_date = datetime(year=save_date.year,month=save_date.month,day=1)
    delta = timedelta(days=1)
    last_month_last_day = first_date - delta
    start_datetime = datetime(year=last_month_last_day.year,month=last_month_last_day.month,day=1)
    end_datetime = last_month_last_day

    if not billingAccount_id:
        container_name = subscription_id
    else:
        container_name = billingAccount_id
    # 1. 从API 获得 上个月的所有数据

    api_df = call_consumption_api(
        client_id=client_id, 
        client_secret=client_secret, 
        tenant_id=tenant_id, 
        subscription_id=subscription_id, 
        billingAccount_id=billingAccount_id,
        base_url=base_url, 
        start_datetime=start_datetime, 
        end_datetime=end_datetime)


    # 1.5 补全所有缺失的CSV 文件
    resultb = save_one_month_lostdata_to_csv(
        storageaccount_name=storageaccount_name,
        storageaccount_key=storageaccount_key,
        container_name= container_name,
        consumption_df=api_df,
        month=start_datetime)
    
    # 2. 获取 CSV 目录所有文件    
    csv_df = load_onemonth_from_csv_without_adjustment(
        storageaccount_name=storageaccount_name, 
        storageaccount_key=storageaccount_key, 
        container_name=container_name, 
        month=last_month_last_day)

    with open('config.json') as config_file:
            config_data = json.load(config_file)  
    itemname_totalcost = config_data.get("itemname_totalcost")
    itemname_adjustment = config_data.get("itemname_adjustment")
    itemname_id = config_data.get("itemname_id")
    itemname_date = config_data.get("itemname_date")

    # 3. 计算 CSV 的 每个资源上个月的总金额
    try:
        #csv_after_group_df = csv_df.groupby(itemname_id)[itemname_totalcost].sum().reset_index()
        cols = csv_df.columns.tolist()  
        # 创建一个字典，其中每个列的默认函数是 'first'  
        agg_dict = {col: 'first' for col in cols}  
        # 对于需要求和的列，将函数改为 'sum'  
        agg_dict.update({itemname_totalcost: 'sum'})  
        # 现在，我们可以使用这个字典进行 groupby 操作  
        csv_after_group_df = csv_df.groupby(itemname_id, as_index=False).agg(agg_dict)  

        # 4. 计算 API 的 每个资源上个月的总金额
        #api_after_group_df = api_df.groupby(itemname_id)[itemname_totalcost].sum().reset_index()
        cols = api_df.columns.tolist()  
        # 创建一个字典，其中每个列的默认函数是 'first'  
        agg_dict = {col: 'first' for col in cols}  
        # 对于需要求和的列，将函数改为 'sum'  
        agg_dict.update({itemname_totalcost: 'sum'})  
        # 现在，我们可以使用这个字典进行 groupby 操作  
        api_after_group_df = api_df.groupby(itemname_id, as_index=False).agg(agg_dict)


    except Exception as e:
        print(f'Error in last month sum: {e}')
        raise e  

    # 5. 逐个资源比对总金额，生成 adjustment
    adjustment_df = pd.DataFrame(columns=api_after_group_df.columns)
    for index, api_after_group_row in api_after_group_df.iterrows():
        for index, csv_after_group_row in csv_after_group_df.iterrows():
            if csv_after_group_row[itemname_id] == api_after_group_row[itemname_id]:
                csv_totalcost_ground = round(csv_after_group_row[itemname_totalcost]*100)/100
                api_totalcost_ground = round(api_after_group_row[itemname_totalcost]*100)/100
                if  csv_totalcost_ground != api_totalcost_ground:
                    i = api_totalcost_ground - csv_totalcost_ground
                    i = round(i*100)/100
                    api_after_group_row[itemname_totalcost] = i
                    api_after_group_row[itemname_date] = save_date.strftime('%Y-%m-%d')
                    adjustment_df = pd.concat([adjustment_df, api_after_group_row.to_frame().T], ignore_index=True)

    adjustment_df[itemname_adjustment] = True
    return adjustment_df


def call_consumption_api(client_id: str, client_secret: str, tenant_id: str, subscription_id:str, billingAccount_id:str, base_url:str, start_datetime: datetime, end_datetime: datetime) -> pd.DataFrame:
    credentials = ClientSecretCredential(
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id
    )
    cost_client = CostManagementClient(credential=credentials, base_url=base_url)
    returndf = pd.DataFrame()
    try:
        operation = cost_client.generate_cost_details_report
        parameters = GenerateCostDetailsReportRequestDefinition(
            metric=CostDetailsMetricType.AMORTIZED_COST_COST_DETAILS_METRIC_TYPE,
            #metric=CostDetailsMetricType.ACTUAL_COST_COST_DETAILS_METRIC_TYPE,
            time_period=CostDetailsTimePeriod(
                start = start_datetime,
                end = end_datetime
            )
        )

        if not billingAccount_id:
            scope = f"/subscriptions/{subscription_id}"
        else:
            scope = f"/providers/Microsoft.Billing/billingAccounts/{billingAccount_id}"
        # 调用 begin_create_operation 方法  
        #poller = operation.begin_create_operation(scope=scope, parameters=parameters)  
        # 在这里发现 可能是由于 Python SDK 的原因，如果 
        # generateCostDetailsReport Response status: 429
        # 则 begin_create_operation 不再返回，可能是 Python SDK 的 bug
        # 通过调用构造的线程，调用 operation.begin_create_operation(scope=scope, parameters=parameters)  
        poller = run_with_timeout(scope, parameters, operation, 120)

        # 异步轮询直到操作完成  
        while not poller.done():  
            print("Operation status: ", poller.status())  
            time.sleep(5)  # 等待一段时间再次轮询  
        
        poller_result = poller.result()  
        
        #再次检查 generateCostDetailsReport error: 429
        #如果有 Error，通过 Exception 扔出去
        if poller_result.error:
            raise CustomException429(f"GenerateCostDetailsReportOperations error: {poller_result.error}, suggest to wait 20 mins and try again")
        
        if poller_result.blob_count > 1:
            #测试专用break
            blobindex = 0

        blobindex = 0
        while blobindex < poller_result.blob_count:
            blobfile = poller_result.blobs[blobindex].blob_link
            df = pd.read_csv(blobfile, encoding='utf-8-sig', dtype={'billingProfileId': str, 'billingAccountId': str})
            returndf=pd.concat([returndf, df], ignore_index=True)
            blobindex = blobindex+1
        
    except Exception as e:
        print(f'Error creating container: {e}')
        raise e  
    
    returndf.rename(columns=lambda x: x[0].lower() + x[1:], inplace=True)

    return returndf


def is_valid_subscription_id(subscription_id):
    # Azure 订阅 ID 的正则表达式
    subscription_id_pattern = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

    # 进行匹配
    match = subscription_id_pattern.match(subscription_id)

    # 如果匹配成功，返回 True，否则返回 False
    return bool(match)


#构建一个线程函数，调用 call_consumption_api 函数中的 poller = operation.begin_create_operation(scope=scope, parameters=parameters) 
class CustomException429(Exception):
    def __init__(self, message="This is a custom exception"):
        self.message = message
        super().__init__(self.message)


class thdLROPoller:
    def __init__(self):
        self.lock = threading.RLock()
        self.result = None

    def update_result(self, new_result):
        with self.lock:
            self.result = new_result


def long_running_function(scope, parameters, operation:GenerateCostDetailsReportOperations, thdpoller:thdLROPoller)-> LROPoller[CostDetailsOperationResults]:
    poller = operation.begin_create_operation(scope=scope, parameters=parameters)
    thdpoller.update_result(poller)


def run_with_timeout(scope, parameters, operation:GenerateCostDetailsReportOperations,timeout)-> LROPoller[CostDetailsOperationResults]:
    stop_event = threading.Event()
    thdpoller = thdLROPoller()
    poller = None

    thread = threading.Thread(target=lambda: long_running_function(scope=scope, parameters=parameters, operation=operation, thdpoller=thdpoller))
    thread.start()
    # 等待超时
    thread.join(timeout)
    if thread.is_alive():
        # 如果线程仍然在运行，说明超时了，设置事件来停止函数
        stop_event.set()
        time.sleep(2)
        raise CustomException429(f"GenerateCostDetailsReportOperations 429, suggest to wait 20 mins and try again")
    
    with thdpoller.lock:
        poller = thdpoller.result
    return poller
            
