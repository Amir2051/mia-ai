from typing import Any, Optional

from app.shopify.client import ShopifyAPIClient, ShopifyAPIError


class OrderService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_orders(self, query: str = '', first: int = 20, after: Optional[str] = None) -> dict:
        cursor = f', after: {after!r}' if after else ''
        gql = (
            'query($query: String, $first: Int) {'
            '  orders(first: $first, reverse: true, query: $query' + cursor + ') {'
            '    edges { cursor node { id name createdAt displayFinancialStatus displayFulfillmentStatus'
            '      totalPriceSet { shopMoney { amount currencyCode } }'
            '      customer { id displayName email }'
            '      lineItems(first: 10) { edges { node { title quantity sku } } }'
            '    } }'
            '    pageInfo { hasNextPage endCursor }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'query': query or None, 'first': first})

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
