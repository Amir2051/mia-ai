from typing import Optional

from app.shopify.client import ShopifyAPIClient


class CustomerService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_customers(self, query: str = '', first: int = 20, after: Optional[str] = None) -> dict:
        # Avoid Level 2 protected customer fields in the list request. Shopify
        # can reject a GraphQL request when an app has customer-data access but
        # has not been granted the corresponding identifying fields (such as
        # name). Details are fetched separately when those fields are approved.
        gql = (
            'query($query: String, $first: Int!, $after: String) {'
            '  customers(first: $first, after: $after, query: $query) {'
            '    edges { cursor node { id numberOfOrders } }'
            '    pageInfo { hasNextPage endCursor }'
            '  }'
            '}'
        )
        return await self.api.graphql(
            gql,
            {'query': query or None, 'first': max(1, min(first, 250)), 'after': after},
        )

    async def get_customer(self, customer_id: str) -> dict:
        gql = (
            'query($id: ID!) {'
            '  customer(id: $id) {'
            '    id displayName firstName lastName tags'
            '    ordersCount'
            '    defaultAddress { address1 city province country zip }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'id': customer_id})
