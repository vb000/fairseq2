# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from pathlib import Path

from fairseq2.assets.card import AssetCard as AssetCard
from fairseq2.assets.card import AssetCardError as AssetCardError
from fairseq2.assets.card import (
    AssetCardFieldNotFoundError as AssetCardFieldNotFoundError,
)
from fairseq2.assets.card_storage import (
    AssetCardNotFoundError as AssetCardNotFoundError,
)
from fairseq2.assets.card_storage import AssetCardStorage as AssetCardStorage
from fairseq2.assets.card_storage import LocalAssetCardStorage as LocalAssetCardStorage
from fairseq2.assets.downloader import AssetDownloader as AssetDownloader
from fairseq2.assets.downloader import AssetDownloadError as AssetDownloadError
from fairseq2.assets.downloader import DefaultAssetDownloader as DefaultAssetDownloader
from fairseq2.assets.error import AssetError as AssetError
from fairseq2.assets.store import AssetStore as AssetStore
from fairseq2.assets.store import DefaultAssetStore as DefaultAssetStore


def _create_asset_store() -> AssetStore:
    pathname = Path(__file__).parent.joinpath("cards")

    card_storage = LocalAssetCardStorage(pathname)

    return DefaultAssetStore(card_storage)


def _create_asset_downloader() -> AssetDownloader:
    return DefaultAssetDownloader(progress=True)


global_asset_store = _create_asset_store()

global_asset_downloader = _create_asset_downloader()
