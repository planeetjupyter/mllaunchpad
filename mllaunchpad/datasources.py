# Stdlib imports
import logging
from typing import Dict, cast

# Third-party imports
import numpy as np
import pandas as pd

# Project imports
from mllaunchpad.resource import DataSink, DataSource, Raw, get_user_pw


logger = logging.getLogger(__name__)

SUPPORTED_FILE_TYPES = ["csv", "euro_csv", "text_file", "binary_file"]


def _get_oracle_connection(dbms_config: Dict):
    import cx_Oracle  # Importing here avoids environment-specific dependencies

    user, pw = get_user_pw(
        dbms_config["user_var"], dbms_config["password_var"]
    )
    dsn_tns = cx_Oracle.makedsn(
        dbms_config["host"],
        dbms_config["port"],
        service_name=dbms_config["service_name"],
    )
    logger.debug("Oracle connection string: %s", dsn_tns)

    kw_options = dbms_config.get("options", {})
    connection = cx_Oracle.connect(user, pw, dsn_tns, **kw_options)

    return connection


class OracleDataSource(DataSource):
    """DataSource for Oracle database connections.

    Creates a long-living connection on initialization.

    Configuration example::

        dbms:
          # ... (other connections)
          my_connection:  # NOTE: You can use the same connection for several datasources and datasinks
            type: oracle
            host: host.example.com
            port: 1251
            user_var: MY_USER_ENV_VAR
            password_var: MY_PW_ENV_VAR  # optional
            service_name: servicename.example.com
            options: {}  # used as **kwargs when initializing the DB connection
        # ...
        datasources:
          # ... (other datasources)
          my_datasource:
            type: dbms.my_connection
            query: SELECT * FROM somewhere.my_table where id = :id  # fill `:params` by calling `get_dataframe` with a `dict`
            expires: 0    # generic parameter, see documentation on DataSources
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when fetching the query using `pandas.read_sql`
    """

    serves = ["dbms.oracle"]

    def __init__(
        self, identifier: str, datasource_config: Dict, dbms_config: Dict
    ):
        super().__init__(identifier, datasource_config)

        self.dbms_config = dbms_config

        logger.info(
            "Establishing Oracle database connection for datasource {}...".format(
                self.id
            )
        )
        self.connection = _get_oracle_connection(dbms_config)

    def get_dataframe(
        self, params: Dict = None, buffer: bool = False
    ) -> pd.DataFrame:
        """Get the data as pandas dataframe.

        Example::

            data_sources["my_datasource"].get_dataframe({"id": 387})

        :param params: Query parameters to fill in query (e.g. replace query's `:id` parameter with value `387`)
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool

        :return: DataFrame object, possibly cached according to config value of `expires:`
        """
        if buffer:
            raise NotImplementedError("Buffered reading not supported yet")

        # TODO: maybe want to open/close connection on every method call (shouldn't happen often)
        query = self.config["query"]
        params = params or {}
        kw_options = self.options

        logger.debug(
            "Fetching query {} with params {} and options {}...".format(
                query, params, kw_options
            )
        )
        df = pd.read_sql(
            query, con=self.connection, params=params, **kw_options
        )
        df.fillna(np.nan, inplace=True)

        return df

    def get_raw(self, params: Dict = None, buffer: bool = False) -> Raw:
        """Not implemented.

        :raises NotImplementedError: Raw/blob format currently not supported.
        """
        raise NotImplementedError(
            "OracleDataSource currently does not not support raw format/blobs. "
            'Use method "get_dataframe" for dataframes'
        )

    def __del__(self):
        if hasattr(self, "connection"):
            self.connection.close()


class FileDataSource(DataSource):
    """DataSource for fetching data from files.

    See :attr:`serves` for the available types.

    Configuration example::

        datasources:
          # ... (other datasources)
          my_datasource:
            type: euro_csv  # `euro_csv` changes separators to ";" and decimals to "," w.r.t. `csv`
            path: /some/file.csv  # Can be URL, uses `pandas.read_csv` internally
            expires: 0    # generic parameter, see documentation on DataSources
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when fetching the data using `pandas.read_csv`
          my_raw_datasource:
            type: text_file  # raw files can also be of type `binary_file`
            path: /some/file.txt  # Can be URL
            expires: 0    # generic parameter, see documentation on DataSources
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when fetching the data using `fh.read`

    Using the raw formats `binary_file` and `text_file`, you can read arbitrary data, as long as
    it can be represented as a `bytes` or a `str` object, respectively. Please note that while possible, it is not
    recommended to persist `DataFrame`s this way, because by adding format-specific code to your
    model, you're giving up your code's independence from the type of `DataSource`/`DataSink`.
    Here's an example for unpickling an arbitrary object::

        # config fragment:
        datasources:
          # ...
          my_pickle_datasource:
            type: binary_file
            path: /some/file.pickle
            tags: [train]
            options: {}

        # code fragment:
        import pickle
        # ...
        # in predict/test/train code:
        my_pickle = data_sources["my_pickle_datasource"].get_raw()
        my_object = pickle.loads(my_pickle)

    """

    serves = SUPPORTED_FILE_TYPES

    def __init__(self, identifier: str, datasource_config: Dict):
        super().__init__(identifier, datasource_config)

        ds_type = datasource_config["type"]
        if ds_type not in SUPPORTED_FILE_TYPES:
            raise TypeError(
                "{} is not a datasource file type (in datasource {}).".format(
                    repr(ds_type), repr(identifier)
                )
            )

        self.type = ds_type
        self.path = datasource_config["path"]

    def get_dataframe(
        self, params: Dict = None, buffer: bool = False
    ) -> pd.DataFrame:
        """Get data as a pandas dataframe.

        Example::

            data_sources["my_datasource"].get_dataframe()

        :param params: Currently not implemented
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool

        :return: DataFrame object, possibly cached according to config value of `expires:`
        """
        if buffer:
            raise NotImplementedError("Buffered reading not supported yet")

        kw_options = self.options

        logger.debug(
            "Loading type {} file {} with options {}...".format(
                self.type, self.path, kw_options
            )
        )
        if self.type == "csv":
            df = pd.read_csv(self.path, **kw_options)
        elif self.type == "euro_csv":
            df = pd.read_csv(self.path, sep=";", decimal=",", **kw_options)
        else:
            raise TypeError(
                'Can only read csv files as dataframes. Use method "get_raw" for raw data'
            )

        return df

    def get_raw(self, params: Dict = None, buffer: bool = False) -> Raw:
        """Get data as raw (unstructured) data.

        Example::

            data_sources["my_raw_datasource"].get_raw()

        :param params: Currently not implemented
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool

        :return: The file's bytes (binary) or string (text) contents, possibly cached according to config value of `expires:`
        :rtype: bytes or str
        """
        if buffer:
            raise NotImplementedError("Buffered reading not supported yet")

        kw_options = self.options

        logger.debug(
            "Loading raw {} {} with options {}...".format(
                self.type, self.path, kw_options
            )
        )

        raw: Raw
        if self.type == "text_file":
            with open(self.path, "r") as txt_file:
                raw = txt_file.read(**kw_options)
        elif self.type == "binary_file":
            with open(self.path, "rb") as bin_file:
                raw = bin_file.read(**kw_options)
        else:
            raise TypeError(
                "Can only read binary data or text strings as raw file. "
                'Use method "get_dataframe" for dataframes'
            )

        return raw


class FileDataSink(DataSink):
    """DataSink for putting data into files.

    See :attr:`serves` for the available types.

    Configuration example::

        datasinks:
          # ... (other datasinks)
          my_datasink:
            type: euro_csv  # `euro_csv` changes separators to ";" and decimals to "," w.r.t. `csv`
            path: /some/file.csv  # Can be URL, uses `df.to_csv` internally
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when fetching the data using `df.to_csv`
          my_raw_datasink:
            type: text_file  # raw files can also be of type `binary_file`
            path: /some/file.txt  # Can be URL
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when writing the data using `fh.write`

    Using the raw formats `binary_file` and `text_file`, you can persist arbitrary data, as long as
    it can be represented as a `bytes` or a `str` object, respectively. Please note that while possible, it is not
    recommended to persist `DataFrame`s this way, because by adding format-specific code to your
    model, you're giving up your code's independence from the type of `DataSource`/`DataSink`.
    Here's an example for pickling an arbitrary object::

        # config fragment:
        datasinks:
          # ...
          my_pickle_datasink:
            type: binary_file
            path: /some/file.pickle
            tags: [train]
            options: {}

        # code fragment:
        import pickle
        # ...
        # in predict/test/train code:
        my_pickle = pickle.dumps(my_object)
        data_sinks["my_pickle_datasink"].put_raw(my_pickle)
    """

    serves = SUPPORTED_FILE_TYPES

    def __init__(self, identifier: str, datasink_config: Dict):
        super().__init__(identifier, datasink_config)

        ds_type = datasink_config["type"]
        if ds_type not in SUPPORTED_FILE_TYPES:
            raise TypeError(
                "{} is not a datasink file type (in datasink {}).".format(
                    repr(ds_type), repr(identifier)
                )
            )

        self.type = ds_type
        self.path = datasink_config["path"]

    def put_dataframe(
        self,
        dataframe: pd.DataFrame,
        params: Dict = None,
        buffer: bool = False,
    ) -> None:
        """Write a pandas dataframe to file.
        The default is not to save the dataframe's row index.
        Configure the DataSink's `options` dict to pass keyword arguments to `my_df.to_csv`.

        Example::

            data_sinks["my_datasink"].put_dataframe(my_df)

        :param dataframe: The pandas dataframe to save
        :type dataframe: pandas DataFrame
        :param params: Currently not implemented
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool
        """
        if buffer:
            raise NotImplementedError("Buffered writing not supported yet")

        kw_options = self.options
        if "index" not in kw_options:
            kw_options["index"] = False

        logger.debug(
            "Writing dataframe to type {} file {} with options {}...".format(
                self.type, self.path, kw_options
            )
        )
        if self.type == "csv":
            dataframe.to_csv(self.path, **kw_options)
        elif self.type == "euro_csv":
            dataframe.to_csv(self.path, sep=";", decimal=",", **kw_options)
        else:
            raise TypeError(
                'Can only write dataframes to csv file. Use method "put_raw" for raw data'
            )

    def put_raw(
        self, raw_data: Raw, params: Dict = None, buffer: bool = False,
    ) -> None:
        """Write raw (unstructured) data to file.

        Example::

            data_sinks["my_raw_datasink"].put_raw(my_data)

        :param raw_data: The data to save (bytes for binary, string for text file)
        :type raw_data: bytes or str
        :param params: Currently not implemented
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool
        """
        if buffer:
            raise NotImplementedError("Buffered writing not supported yet")

        kw_options = self.options

        logger.debug(
            "Writing raw {} file {} with options {}...".format(
                self.type, self.path, kw_options
            )
        )
        if self.type == "text_file":
            with open(self.path, "w", **kw_options) as txt_file:
                raw_str: str = cast(str, raw_data)
                txt_file.write(raw_str)
        elif self.type == "binary_file":
            with open(self.path, "wb", **kw_options) as bin_file:
                raw_bytes: bytes = cast(bytes, raw_data)
                bin_file.write(raw_bytes)
        else:
            raise TypeError(
                "Can only write binary data or text strings as raw file. "
                + 'Use method "put_dataframe" for dataframes'
            )


class OracleDataSink(DataSink):
    """DataSink for Oracle database connections.

    Creates a long-living connection on initialization.

    Configuration example::

        dbms:
          # ... (other connections)
          my_connection:  # NOTE: You can use the same connection for several datasources and datasinks
            type: oracle
            host: host.example.com
            port: 1251
            user_var: MY_USER_ENV_VAR
            password_var: MY_PW_ENV_VAR  # optional
            service_name: servicename.example.com
            options: {}  # used as **kwargs when initializing the DB connection
        # ...
        datasinks:
          # ... (other datasinks)
          my_datasink:
            type: dbms.my_connection
            table: somewhere.my_table
            tags: [train] # generic parameter, see documentation on DataSources and DataSinks
            options: {}   # used as **kwargs when fetching the query using `pandas.to_sql`
    """

    serves = ["dbms.oracle"]

    def __init__(
        self, identifier: str, datasink_config: Dict, dbms_config: Dict
    ):
        super().__init__(identifier, datasink_config)

        self.dbms_config = dbms_config

        logger.info(
            "Establishing Oracle database connection for datasource {}...".format(
                self.id
            )
        )

        self.connection = _get_oracle_connection(dbms_config)

    def put_dataframe(
        self,
        dataframe: pd.DataFrame,
        params: Dict = None,
        buffer: bool = False,
    ) -> None:
        """Store the pandas dataframe as a table.
        The default is not to store the dataframe's row index.
        Configure the DataSink's options dict to pass keyword arguments to `df.o_sql`.

        Example::

            data_sinks["my_datasink"].put_dataframe(my_df)

        :param dataframe: The pandas dataframe to store
        :type dataframe: pandas DataFrame
        :param params: Currently not implemented
        :type params: optional dict
        :param buffer: Currently not implemented
        :type buffer: optional bool
        """
        if buffer:
            raise NotImplementedError("Buffered storing not supported yet")

        # TODO: maybe want to open/close connection on every method call (shouldn't happen often)
        table = self.config["table"]
        kw_options = self.options
        if "index" not in kw_options:
            kw_options["index"] = False

        logger.debug(
            "Storing data in table {} with options {}...".format(
                table, kw_options
            )
        )
        dataframe.to_sql(table, con=self.connection, **kw_options)

    def put_raw(
        self, raw_data, params: str = None, buffer: bool = False
    ) -> None:
        """Not implemented.

        :raises NotImplementedError: Raw/blob format currently not supported.
        """
        raise NotImplementedError(
            "OracleDataSink currently does not not support raw format/blobs. "
            'Use method "put_dataframe" for raw data'
        )

    def __del__(self):
        if hasattr(self, "connection"):
            self.connection.close()