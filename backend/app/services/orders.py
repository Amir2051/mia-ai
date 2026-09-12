from typing import Optional

from app.shopify.client import ShopifyAPIClient


class OrderService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_orders(self, query: str = '', first: int = 20, after: Optional[str] = None) -> dict:
        gql = (
            'query($query: String, $first: Int!, $after: String) {'
            '  orders(first: $first, after: $after, reverse: true, query: $query) {'
            '    edges { cursor node { id name createdAt displayFinancialStatus displayFulfillmentStatus'
            '      totalPriceSet { shopMoney { amount currencyCode } }'
            '      customer { id displayName email }'
            '      lineItems(first: 10) { edges { node { title quantity sku } } }'
            '    } }'
            '    pageInfo { hasNextPage endCursor }'
            '  }'
            '}'
        )
        return await self.api.graphql(
            gql,
            {'query': query or None, 'first': max(1, min(first, 250)), 'after': after},
        )

    async def get_order(self, order_id: str) -> dict:
        gql = (
            'query($id: ID!) {'
            '  order(id: $id) {'
            '    id name email'
            '    shippingAddress { name address1 address2 city province country zip phone }'
            '    lineItems(first: 50) { edges { node { title quantity variant { sku } originalUnitPriceSet { shopMoney { amount currencyCode } } } } }'
            '    transactions { id kind status amountSet { shopMoney { amount currencyCode } } }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'id': order_id})
