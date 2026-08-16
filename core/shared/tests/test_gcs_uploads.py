from unittest.mock import patch

from django.test import override_settings


@override_settings(GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket')
def test_read_upload_from_public_url_extracts_path_after_bucket():
    from core.shared.gcs_uploads import read_upload_from_public_url
    with patch('core.shared.gcs_uploads.read_upload', return_value=b'bytes') as mock_read:
        data = read_upload_from_public_url(
            'https://storage.googleapis.com/test-bucket/posts/dia-1.png'
        )
    assert data == b'bytes'
    mock_read.assert_called_once_with('posts/dia-1.png')


@override_settings(GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket')
def test_read_upload_from_public_url_strips_query_string():
    from core.shared.gcs_uploads import read_upload_from_public_url
    with patch('core.shared.gcs_uploads.read_upload', return_value=b'bytes') as mock_read:
        read_upload_from_public_url(
            'https://storage.googleapis.com/test-bucket/posts/dia-1.png?t=1755300000'
        )
    mock_read.assert_called_once_with('posts/dia-1.png')
