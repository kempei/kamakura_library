from distutils.util import change_root
from email.headerregistry import ContentTypeHeader
from selenium.webdriver.chrome.webdriver import WebDriver
from logzero import logger
import logzero
import json

from selenium import webdriver
from selenium.webdriver.remote.webelement import WebElement 
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec

from selenium import webdriver

import os, time

import boto3
import botocore

class KamakuraLibrary():
    def init(self):
        if not 'KAMALIB_IDINFO' in os.environ:
            raise ValueError('env KAMALIB_IDINFO is not found.')
        if not 'KAMALIB_S3BUCKET' in os.environ:
            raise ValueError('env KAMALIB_S3BUCKET is not found.')
        if not 'KAMALIB_S3KEY' in os.environ:
            raise ValueError('env KAMALIB_S3KEY is not found.')
        logger.info("初期化処理を開始します")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=900x1200")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-sandbox")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--v=99")
        #options.add_argument("--single-process")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--homedir=/tmp")
        options.add_argument('--user-agent=Mozilla/5.0')
        options.add_argument('--disable-dev-shm-usage')
        self.driver:WebDriver = webdriver.Chrome(options=options)
        self.driver.implicitly_wait(10)
        self.wait:WebDriverWait = WebDriverWait(self.driver, 30)

    def login(self):
        d = self.driver
        w = self.wait
        kamalib_idinfo:dict[str, str] = json.loads(os.environ['KAMALIB_IDINFO'])
        all_books:dict = dict()
        for name, idinfo in kamalib_idinfo.items():
            logger.info(f"ログインします[{name}]")
            usercardno, userpasswd = idinfo.split(':')
            d.get('https://lib.city.kamakura.kanagawa.jp/')
            w.until(ec.presence_of_all_elements_located)
            self.wait_until(By.ID, "sidebar_left")
            sidebar:WebElement = d.find_element_by_id('sidebar_left')
            sidebar.find_element_by_class_name('header_button').click()
            w.until(ec.presence_of_all_elements_located)
            d.find_element_by_name('usercardno').send_keys(usercardno)
            d.find_element_by_name('userpasswd').send_keys(userpasswd)
            d.find_element_by_name('Login').click()
            w.until(ec.presence_of_all_elements_located)

            # 更新ボタンを全部押す
            counter = 0
            while True:
                buttons = d.find_elements_by_xpath('//button[@value="更新する"]')
                if len(buttons) == 0:
                    break
                counter += 1
                buttons[0].click()
                w.until(ec.visibility_of_element_located((By.NAME, "chkLKOUSIN")))
                d.find_element_by_name('chkLKOUSIN').click()
                logger.debug("push deadline update button")
                time.sleep(1)
            if counter > 0:
                logger.info(f"{counter}回の更新ボタンを押しました[{name}]")

            # 貸し出し図書情報を収集する
            logger.info(f"貸し出し図書情報を収集します[{name}]")
            table_list:list = d.find_elements_by_xpath('//*[@id="ContentLend"]/form/div[2]/table')
            if len(table_list) > 0:
                table:WebElement = table_list[0]
                trs:list[WebElement] = table.find_elements_by_xpath('tbody/tr')
                books:list[str] = list()
                for tr in trs:
                    book:dict = dict()
                    tds:list[WebElement] = tr.find_elements_by_xpath('td')
                    if len(tds) != 9:
                        continue
                    update_txt:str = tds[1].text
                    book['title'] = tds[2].text
                    book['deadline'] = tds[8].text
                    book['booking_request'] = len(update_txt) > 0 and "予約" in update_txt
                    books.append(book)
                all_books[name] = books
            else:
                logger.info(f"{name}は借りている本がありません")
            logout_button:WebElement = d.find_element_by_xpath('//button[@onclick="OPWUSERLOGOUT(1)"]')
            logout_button.click()
            w.until(ec.presence_of_all_elements_located)
        logger.debug(all_books)
        return all_books
        
    def close(self):
        try:
            self.driver.close()
        except:
            logger.debug("Ignore exception (close)")
        try:
            self.driver.quit()
        except:
            logger.debug("Ignore exception (quit)")
    
    def wait_until(self, by, loc):
        d = self.driver
        success_counter = 0
        failed_counter = 0
        while True:
            e = d.find_elements(by, loc)
            if len(e) > 0:
                failed_counter = 0
                success_counter += 1
                if success_counter > 10:
                    return
            else:
                success_counter = 0
                failed_counter += 1
                if failed_counter > 300:
                    raise RuntimeError(f"WebElement timeout while waiting [{loc}]")
            time.sleep(0.1)

    def upload(self, data:dict):
        logger.info(f"貸し出し履歴をS3からダウンロードします")
        s3 = boto3.resource('s3')
        s3bucket = os.environ['KAMALIB_S3BUCKET']
        s3key = os.environ['KAMALIB_S3KEY']
        folder = os.path.dirname(s3key)
        basename = os.path.splitext(os.path.basename(s3key))[0]
        s3key_history = f"{folder}/{basename}-history.json"
        history_obj = s3.Object(s3bucket, s3key_history)
        history_dict:dict = {}
        try:
            history_dict = json.loads(history_obj.get()['Body'].read().decode('utf-8'))
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.debug("history not found.")
            else:
                raise e
        logger.info(f"現在の貸し出し図書をS3へアップロードします")
        obj = s3.Object(s3bucket, s3key)
        r = obj.put(Body = json.dumps(data))
        if r['ResponseMetadata']['HTTPStatusCode'] != 200:
            raise RuntimeError(f's3 upload response:{r}')

        change_flag = False
        for name, books in data.items():
            for book in books:
                title = book["title"]
                deadline = book["deadline"]
                if title in history_dict:
                    if not deadline in history_dict[title]:
                        logger.debug(f"貸し出し履歴の [{title}] に新たな返却日を追加しました")
                        history_dict[title].append(deadline)
                        change_flag = True
                else:
                    logger.debug(f"貸し出し履歴に [{title}] を追加しました")
                    history_dict[title] = [deadline]
                    change_flag = True
        if change_flag:
            logger.info(f"貸し出し履歴をS3へアップロードします")
            history_obj = s3.Object(s3bucket, s3key_history)
            r = history_obj.put(Body = json.dumps(history_dict))
            if r['ResponseMetadata']['HTTPStatusCode'] != 200:
                raise RuntimeError(f's3 upload response:{r}')

if __name__ == "__main__":
    if "LOG_LEVEL" in os.environ:
        logzero.loglevel(int(os.environ["LOG_LEVEL"]))
    kama = KamakuraLibrary()
    try:
        kama.init()
        json_data = kama.login()
        kama.upload(json_data)
        logger.info("すべての処理が正常に終了しました")
    finally:
        kama.close()
