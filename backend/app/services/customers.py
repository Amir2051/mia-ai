from typing import Any, Optional

from app.shopify.client import ShopifyAPIClient, ShopifyAPIError


class CustomerService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_customers(self, query: str = '', first: int = 20, after: Optional[str] = None) -> dict:
        cursor = f', after: {after!r}' if after else ''
        gql = (
            'query($query: String, $first: Int) {'
            '  customers(first: $first, query: $query' + cursor + ') {'
            '    edges { cursor node { id email displayName numberOfOrders amountSpent { amount currencyCode } } }'
            '    pageInfo { hasNextPage endCursor }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'query': query or None, 'first': first})

    async def get_customer(self, customer_id: str) -> dict:
        gql = (
            'query($id: ID!) {'
            '  customer(id: $id) {'
            '    id email displayName firstName lastName tags'
            '    ordersCount amountSpent { amount currencyCode }'
            '    defaultAddress { address1 city province country zip }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'id': customer_id})
