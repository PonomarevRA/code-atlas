/** A mod imports no Atlas internals; API v1 owns the stable extension boundary. */
export function activate(api) {
  api.registerCardDecorator(({overlay,bounds})=>{
    const tag=document.createElementNS('http://www.w3.org/2000/svg','text');
    tag.setAttribute('x',bounds.left+bounds.width-12);tag.setAttribute('y',bounds.top+12);
    tag.setAttribute('text-anchor','end');tag.setAttribute('font-size',7);tag.setAttribute('fill','#537ca7');
    tag.setAttribute('pointer-events','none');tag.textContent='MOD';overlay.append(tag);
  });
}
