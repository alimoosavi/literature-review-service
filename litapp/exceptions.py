"""
litapp/exceptions.py
Custom exception handler for better error responses
"""

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that provides consistent error responses
    and logs exceptions appropriately.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)

    if response is not None:
        # Customize the response data
        custom_response_data = {
            'error': True,
            'message': None,
            'details': None,
            'status_code': response.status_code
        }

        # Extract error message
        if isinstance(response.data, dict):
            if 'detail' in response.data:
                custom_response_data['message'] = str(response.data['detail'])
            else:
                custom_response_data['details'] = response.data
                custom_response_data['message'] = 'Validation error occurred'
        elif isinstance(response.data, list):
            custom_response_data['message'] = str(response.data[0]) if response.data else 'An error occurred'
        else:
            custom_response_data['message'] = str(response.data)

        response.data = custom_response_data

        # Log the error
        logger.warning(
            f"API Error: {custom_response_data['message']} "
            f"(Status: {response.status_code}, View: {context.get('view', 'Unknown')})"
        )
    else:
        # Handle unexpected errors
        logger.exception(f"Unhandled exception in view {context.get('view', 'Unknown')}: {exc}")

        response = Response(
            {
                'error': True,
                'message': 'An unexpected error occurred. Please try again later.',
                'details': str(exc) if settings.DEBUG else None,
                'status_code': status.HTTP_500_INTERNAL_SERVER_ERROR
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return response
