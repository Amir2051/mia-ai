from typing import Optional

from app.shopify.client import ShopifyAPIClient


class CustomerService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_customers(self, query: str = '', first: int = 20, after: Optional[str] = None) -> dict:
        gql = (
            'query($query: String, $first: Int!, $after: String) {'
            '  customers(first: $first, after: $after, query: $query) {'
            '    edges { cursor node { '
            '      id firstName lastName '
            '      defaultEmailAddress { emailAddress } '
            '      defaultPhoneNumber { phoneNumber } '
            '      numberOfOrders amountSpent { amount currencyCode } '
            '    } }'
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
            '    id firstName lastName '
            '    defaultEmailAddress { emailAddress } '
            '    defaultPhoneNumber { phoneNumber } '
            '    numberOfOrders amountSpent { amount currencyCode } tags'
            '    orders(first: 10, reverse: true) {'
            '      edges { node { id name createdAt totalPriceSet { shopMoney { amount currencyCode } } } }'
            '    }'
            '  }'
            '}'
        )
        return await self.api.graphql(gql, {'id': customer_id})
