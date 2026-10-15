from abc import ABC

from parser.utils.configLoader import ConfigLoader

from parser.pipeline.excelReader import ExcelReader
from parser.pipeline.sheetSelector import SheetSelector
from parser.pipeline.headerMapper import HeaderMapper
from parser.pipeline.transactionExtractor import TransactionExtractor

from parser.validators.headerValidator import HeaderValidator


class BaseParser(ABC):

    CONFIG_FILE = None

    def parse(self, file):
        # 1: load
        config = ConfigLoader.load(self.CONFIG_FILE)

        # 2: read
        sheets = ExcelReader.read(file)

        # 3: Select Transaction Sheet
        selected_sheet = SheetSelector.select(sheets)

        dataframe = selected_sheet["dataframe"]

        sheet_name = selected_sheet["sheetName"]

        # 4: Map Headers
        mapper = HeaderMapper(config)

        mapped_headers = mapper.map_headers(dataframe)

        # 5: Validate Headers
        validator = HeaderValidator(config)

        validation = validator.validate(mapped_headers)

        # Stop parsing if required headers are missing
        if not validation["canContinue"]:

            return {

                "success": False,

                "sheetName": sheet_name,

                "headers": list(dataframe.columns),

                "mappedHeaders": mapped_headers,

                "validation": validation,

                "transactions": [],

                "statistics": {}
            }

        # 6 : Extract Transactions
        extraction_result = TransactionExtractor.extract(
            dataframe,
            mapped_headers,
            self.PARSER_TYPE
        )

        #  7 : Return Parse Result
        return {

            "success": True,

            "sheetName": sheet_name,

            "headers": list(dataframe.columns),

            "mappedHeaders": mapped_headers,

            "validation": validation,

            "transactions": extraction_result["transactions"],

            "statistics": extraction_result["statistics"]

        }