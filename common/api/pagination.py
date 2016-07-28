# coding: utf-8
from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param


class CustomPageNumberPagination(PageNumberPagination):
    """
    Pagination personnalisée pour les API et les API views
    """
    page_query_param = 'page'
    page_size_query_param = 'page_size'
    _query_params = [page_query_param, page_size_query_param]
    additional_data = {}

    def get_index_link(self, index):
        if not index:
            return None
        url = self.request and self.request.build_absolute_uri() or ''
        return replace_query_param(url, 'page', index)

    def get_paginated_response(self, data):
        count = self.page.paginator.count
        page_count = self.page.paginator.num_pages
        next = self.page.next_page_number() if self.page.has_next() else None
        previous = self.page.previous_page_number() if self.page.has_previous() else None
        page_size = self.get_page_size(self.request)

        additional_data = OrderedDict((key, value) for key, value in self.additional_data.items())
        response = OrderedDict()
        response.update(OrderedDict([
            ('count', count),
            ('page_size', page_size),
            ('page', self.page.number),
            ('pages', page_count),
            ('previous_page', previous),
            ('next_page', next),
            ('previous', self.get_previous_link()),
            ('next', self.get_next_link()),
            ('first', self.get_index_link(1)),
            ('last', self.get_index_link(page_count)),
            ('results', data),
        ]))
        response.update(additional_data)
        return Response(response)
